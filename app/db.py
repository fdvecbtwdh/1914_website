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
        conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
        _migrate(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(cards)")]
    if "tag" not in cols:
        conn.execute("ALTER TABLE cards ADD COLUMN tag TEXT NOT NULL DEFAULT '正式'")
        # 存量卡牌：名字/ID 带测试标记的归为测试卡
        conn.execute("UPDATE cards SET tag='测试' "
                     "WHERE lower(COALESCE(game_id,'')) LIKE '%test%' OR name LIKE '%测试%'")
    icols = [r[1] for r in conn.execute("PRAGMA table_info(issues)")]
    if "component" not in icols:
        # Issue 所属：game=游戏本体 / web=网页（同步分流到对应仓库）
        conn.execute("ALTER TABLE issues ADD COLUMN component TEXT NOT NULL DEFAULT 'game'")
    ucols = [r[1] for r in conn.execute("PRAGMA table_info(users)")]
    if "security_question" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN security_question TEXT")
    if "security_answer_hash" not in ucols:
        conn.execute("ALTER TABLE users ADD COLUMN security_answer_hash TEXT")
