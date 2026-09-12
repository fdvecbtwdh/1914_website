"""空数据库自动播种 — 实现删除数据库后启动即自动恢复。
触发条件：users 表为空（全新数据库）。已有任何用户时绝不触碰。
播种内容：官方卡牌（游戏项目或 seed/ 回退）+ 默认标签 + 初始管理员 admin（随机密码，
打印到启动控制台与 service 日志，请登录后立即修改）。
"""
import secrets

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
