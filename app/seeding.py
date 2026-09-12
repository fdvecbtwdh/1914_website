"""数据库启动准备与空库自动播种 — 删除数据库后启动即自动恢复。

启动流程（prepare_database）：
1. 库文件不存在，且存在默认模板 data/default.db → 整库复制模板（恢复保存时的完整数据）
2. 否则建表（幂等迁移）
3. 空库且开启 AUTO_SEED → 播种官方卡牌（游戏项目或 seed/ 回退）+ 默认标签
   + 初始管理员 admin（随机密码打印到启动控制台与 service 日志）

保存"当前数据库"为新默认模板：python manage.py save-default
"""
import secrets
import shutil
from pathlib import Path

from . import db
from .card_sync import import_official_cards

log = __import__("logging").getLogger("1914.seed")

DEFAULT_LABELS = [
    ("平衡性", "#c9a227"), ("趣味", "#6b7a4f"), ("自制阵营", "#3a7bbf"),
    ("崩溃", "#b03030"), ("联网", "#3a7bbf"), ("UI", "#8e6aa8"),
    ("数值错误", "#d35400"), ("汉化", "#9b59b6"),
]


def auto_seed(app) -> None:
    if db.query("SELECT COUNT(*) AS n FROM users", one=True)["n"] > 0:
        return  # 已有用户：不是空库，不播种

    result = import_official_cards()

    for name, color in DEFAULT_LABELS:
        if db.query("SELECT 1 FROM labels WHERE name = ?", (name,), one=True) is None:
            db.execute("INSERT INTO labels (name, color) VALUES (?,?)", (name, color))

    from .auth import hash_password
    password = secrets.token_urlsafe(9)  # 12 位随机密码，含大小写数字
    db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?,?,'admin')",
        ("admin", hash_password(password)))

    msg = (f"[1914] 检测到全新数据库，已自动初始化：官方卡牌 {result['created']} 张、"
           f"默认标签 {len(DEFAULT_LABELS)} 个；"
           f"初始管理员 -> 用户名: admin  密码: {password}  （请立即登录修改）")
    log.warning(msg)
    print(msg)


def template_path(app) -> str:
    override = app.config.get("DB_TEMPLATE_PATH")
    if override:
        return override
    return str(Path(app.config["DB_PATH"]).parent / "default.db")


def prepare_database(app) -> str:
    """启动时准备数据库。返回 'template'（模板恢复）/ 'seeded'（空库播种）/ 'existing'（已有数据）。"""
    db_path = app.config["DB_PATH"]
    template = template_path(app)
    restored = False
    if not Path(db_path).exists() and Path(template).exists():
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(template, db_path)
        restored = True
        log.warning("[1914] 数据库不存在，已从默认模板恢复: %s", template)

    db.init_db(db_path)  # 幂等：建表 + 轻量迁移

    if restored:
        state = "template"
    else:
        with app.app_context():
            if app.config.get("AUTO_SEED", True):
                before = db.query("SELECT COUNT(*) AS n FROM users", one=True)["n"]
                auto_seed(app)
                after = db.query("SELECT COUNT(*) AS n FROM users", one=True)["n"]
                state = "seeded" if after > before else "existing"
            else:
                state = "existing"
    return state
