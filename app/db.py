"""SQLite 访问层 — 所有 SQL 集中在此，参数化查询防注入。
设计上保持 SQL 语法标准，便于未来迁移 PostgreSQL。
"""
import sqlite3
from pathlib import Path

from flask import g, has_app_context, current_app

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# CLI（manage.py）在无 Flask 上下文时使用的独立连接
_standalone: sqlite3.Connection | None = None
_standalone_path: str | None = None


def _default_db_path() -> str:
    from .config import Config
    return Config.DB_PATH


def get_db() -> sqlite3.Connection:
    if has_app_context():
        if "db" not in g:
            g.db = _connect(current_app.config["DB_PATH"])
        return g.db
    # 无应用上下文（CLI）：返回模块级独立连接
    global _standalone, _standalone_path
    path = _default_db_path()
    if _standalone is None or _standalone_path != path:
        if _standalone is not None:
            _standalone.close()
        _standalone = _connect(path)
        _standalone_path = path
    return _standalone


def _connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def close_db(_e=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def query(sql: str, args: tuple = (), one: bool = False):
    """SELECT 帮助函数。one=True 返回单行或 None。"""
    cur = get_db().execute(sql, args)
    rows = cur.fetchall()
    cur.close()
    if one:
        return rows[0] if rows else None
    return rows


def execute(sql: str, args: tuple = ()) -> int:
    """INSERT/UPDATE/DELETE，返回 lastrowid。"""
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.lastrowid


def update(sql: str, args: tuple = ()) -> int:
    """UPDATE/DELETE，返回受影响行数。"""
    db = get_db()
    cur = db.execute(sql, args)
    db.commit()
    return cur.rowcount


def init_db(db_path: str) -> None:
    """初始化数据库结构（幂等），并做轻量迁移（如补新增列）。"""
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        _pre_migrate(conn)
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _pre_migrate(conn: sqlite3.Connection) -> None:
    """旧版 notifications 表（从未使用）先删除，让新结构随 schema 重建。"""
    tcols = [r[1] for r in conn.execute("PRAGMA table_info(notifications)")]
    if tcols and "recipient_id" not in tcols:
        conn.execute("DROP TABLE notifications")


def _migrate(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    if "tag" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN tag TEXT NOT NULL DEFAULT '正式'")
        # 存量卡牌：名字/ID 带测试标记的归为测试卡
        conn.execute("UPDATE cards SET tag='测试' "
                     "WHERE lower(COALESCE(game_id,'')) LIKE '%test%' OR name LIKE '%测试%'")
    ccols = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    if "cost_oil" not in ccols:
        # 油费（卡牌正式数据属性；旧卡默认 0=无油费）
        conn.execute("ALTER TABLE cards ADD COLUMN cost_oil INTEGER NOT NULL DEFAULT 0")
    if "cost_k" in ccols and "cost_z" not in ccols:
        # 游戏设计变更：部署消耗改为战争点 Z
        conn.execute("ALTER TABLE cards RENAME COLUMN cost_k TO cost_z")
    icols = [r[1] for r in conn.execute("PRAGMA table_info(issues)")]
    if "component" not in icols:
        # Issue 所属：game=游戏本体 / web=网页（同步分流到对应仓库）
        conn.execute("ALTER TABLE issues ADD COLUMN component TEXT NOT NULL DEFAULT 'game'")
    ucols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "security_question" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
    if "security_answer_hash" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN security_answer_hash TEXT")
    if "last_viewed_messages_at" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN last_viewed_messages_at TEXT")
    ncols = [r[1] for r in conn.execute("PRAGMA table_info(notifications)")]
    if ncols and "forum_post_id" not in ncols:
        # 论坛回复通知需要指向论坛帖子
        conn.execute("ALTER TABLE notifications ADD COLUMN forum_post_id INTEGER")
    if ncols and "detail" not in ncols:
        # 系统类消息（处罚通知）的直接文案
        conn.execute("ALTER TABLE notifications ADD COLUMN detail TEXT")
    ucols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if ucols:
        for col, ddl in (("mute_until", "TEXT"), ("mute_reason", "TEXT"),
                         ("ban_until", "TEXT"), ("banned_reason", "TEXT")):
            if col not in ucols:
                conn.execute(f"ALTER TABLE users ADD COLUMN {col} {ddl}")
    qcols = [r[1] for r in conn.execute("PRAGMA table_info(sync_queue)")]
    if qcols and "repo" not in qcols:
        # 旧库的同步队列表缺 repo 列（按 Issue 所属分流目标仓库），
        # 缺列会让 enqueue 的 INSERT 静默失败、同步永远不入队
        conn.execute("ALTER TABLE sync_queue ADD COLUMN repo TEXT")
