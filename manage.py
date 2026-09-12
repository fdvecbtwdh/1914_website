"""站点管理命令（在项目根目录运行）：
  python manage.py init-db            初始化/升级数据库结构
  python manage.py import-cards       从游戏项目导入官方卡牌
  python manage.py create-admin       创建管理员（交互输入或 --username/--password）
  python manage.py create-labels      创建初始标签
  python manage.py stats              查看数据统计
"""
import argparse
import getpass
import sys

from app import db
from app.auth import hash_password, check_password_strength
from app.config import Config


def init_db(_args):
    db.init_db(Config.DB_PATH)
    print(f"[OK] 数据库已初始化: {Config.DB_PATH}")


def import_cards(_args):
    from app.card_sync import import_official_cards, load_game_cards
    cards = load_game_cards()
    print(f"发现 {len(cards)} 张卡牌（游戏项目不存在时自动使用内置 seed/ 副本）")
    result = import_official_cards()
    print(f"[OK] 官方卡牌导入完成: 新建 {result['created']} / 更新 {result['updated']}")


def save_default(_args):
    """把当前数据库保存为重置默认模板（data/default.db）。
    使用 SQLite backup API，网站运行中也可安全执行。
    """
    import sqlite3
    from pathlib import Path
    db.init_db(Config.DB_PATH)  # 确保存在
    src_conn = sqlite3.connect(Config.DB_PATH)
    dst = Path(Config.DB_PATH).parent / "default.db"
    dst_conn = sqlite3.connect(dst)
    with dst_conn:
        src_conn.backup(dst_conn)
    dst_conn.close()
    src_conn.close()
    print(f"[OK] 当前数据库已保存为重置默认模板: {dst}")
    print("     之后删除数据库文件再启动，将自动恢复为此状态。")


def create_admin(args):
    db.init_db(Config.DB_PATH)
    username = args.username or input("管理员用户名: ").strip()
    password = args.password or getpass.getpass("管理员密码: ")
    if args.password is None:
        confirm = getpass.getpass("确认密码: ")
        if password != confirm:
            sys.exit("两次密码不一致")
    err = check_password_strength(password)
    if err:
        sys.exit(f"密码不合格: {err}")
    if db.query("SELECT 1 FROM users WHERE username = ?", (username,), one=True):
        # 已存在则提升为 admin
        db.execute("UPDATE users SET role = 'admin' WHERE username = ?", (username,))
        print(f"[OK] 用户 {username} 已存在，已提升为 admin")
        return
    uid = db.execute(
        "INSERT INTO users (username, password_hash, role) VALUES (?,?,'admin')",
        (username, hash_password(password)))
    print(f"[OK] 管理员已创建 (id={uid}): {username}")
    print("     请尽快到 /settings 修改密码，并在 .env 中不要保留明文。")


def create_labels(_args):
    db.init_db(Config.DB_PATH)
    labels = [
        ("平衡性", "#c9a227"), ("趣味", "#6b7a4f"), ("自制阵营", "#3a7bbf"),
        ("崩溃", "#b03030"), ("联网", "#3a7bbf"), ("UI", "#8e6aa8"),
        ("数值错误", "#d35400"), ("汉化", "#9b59b6"),
    ]
    n = 0
    for name, color in labels:
        if db.query("SELECT 1 FROM labels WHERE name = ?", (name,), one=True) is None:
            db.execute("INSERT INTO labels (name, color) VALUES (?,?)", (name, color))
            n += 1
    print(f"[OK] 初始标签创建完成（新增 {n} 个）")


def stats(_args):
    db.init_db(Config.DB_PATH)
    for table in ("users", "cards", "issues", "comments", "votes", "reports"):
        row = db.query(f"SELECT COUNT(*) AS n FROM {table}", one=True)
        print(f"{table:>10}: {row['n']}")


def main():
    parser = argparse.ArgumentParser(description="1914.fun 站点管理")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db", help="初始化数据库结构").set_defaults(func=init_db)
    sub.add_parser("import-cards", help="导入官方卡牌").set_defaults(func=import_cards)
    p_admin = sub.add_parser("create-admin", help="创建管理员账号")
    p_admin.add_argument("--username", default="")
    p_admin.add_argument("--password", default=None)
    p_admin.set_defaults(func=create_admin)
    sub.add_parser("create-labels", help="创建初始标签").set_defaults(func=create_labels)
    sub.add_parser("stats", help="数据统计").set_defaults(func=stats)
    sub.add_parser("save-default", help="把当前数据库保存为重置默认模板").set_defaults(func=save_default)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
