"""应用配置 — 全部来自环境变量 / .env，绝不硬编码秘密。"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    """极简 .env 加载器（避免额外依赖）。已存在的环境变量优先。"""
    env_file = BASE_DIR / ".env"
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


_load_dotenv()


def _bool(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).lower() in ("1", "true", "yes", "on")


class Config:
    # --- 基础 ---
    SECRET_KEY = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)
    SITE_NAME = os.environ.get("SITE_NAME", "1914")
    SITE_DOMAIN = os.environ.get("SITE_DOMAIN", "1914.fun")
    SITE_URL = os.environ.get("SITE_URL", "https://1914.fun").rstrip("/")
    SITE_DESCRIPTION = os.environ.get(
        "SITE_DESCRIPTION", "1914 — 第一次世界大战题材卡牌对战游戏官方网站：卡牌社区、玩家投稿与 Bug 反馈平台"
    )

    # --- 数据库 / 存储 ---
    # DATABASE_URL 形如 sqlite:///D:/Code/1914_website/data/1914.db
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url.startswith("sqlite:///"):
        DB_PATH = db_url[len("sqlite:///"):]
    else:
        DB_PATH = str(BASE_DIR / "data" / "1914.db")
    UPLOAD_DIR = Path(os.environ.get("UPLOAD_DIR", str(BASE_DIR / "data" / "uploads")))
    MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "4"))

    # --- 游戏项目（用于导入官方卡牌） ---
    GAME_PROJECT_PATH = os.environ.get("GAME_PROJECT_PATH", r"D:\Code\1914")
    GAME_VERSION = os.environ.get("GAME_VERSION", "0.3.0")
    GAME_VERSION_LABEL = os.environ.get("GAME_VERSION_LABEL", "阶段3 · 15卡+迷雾")
    GITHUB_REPO = os.environ.get("GITHUB_REPO", "fdvecbtwdh/1914")
    GITHUB_REPO_URL = os.environ.get("GITHUB_REPO_URL", "https://github.com/fdvecbtwdh/1914")
    # 网页 Issue 同步目标仓库（与游戏仓库共用 GITHUB_TOKEN）
    GITHUB_WEB_REPO = os.environ.get("GITHUB_WEB_REPO", "fdvecbtwdh/1914_website")
    # 网站自身的源码仓库（页脚展示）
    SITE_REPO_URL = os.environ.get("SITE_REPO_URL", "https://github.com/fdvecbtwdh/1914_website")

    # --- GitHub 同步（可选，缺失时站点照常运行） ---
    GITHUB_TOKEN = os.environ.get("GITHUB_TOKEN", "")
    GITHUB_SYNC_ENABLED = _bool("GITHUB_SYNC_ENABLED", "1")
    GITHUB_SYNC_INTERVAL = int(os.environ.get("GITHUB_SYNC_INTERVAL", "30"))

    # --- 游戏服务器心跳（状态页 /status 预留接口） ---
    # 设置后，游戏服务端按 docs/web-integration.md 协议推送心跳；留空则状态页显示"不可用"
    GAME_SERVER_TOKEN = os.environ.get("GAME_SERVER_TOKEN", "")

    # --- 状态页采样 ---
    STATUS_SAMPLER_ENABLED = _bool("STATUS_SAMPLER_ENABLED", "1")

    # --- 空数据库自动播种（官方卡牌 + 标签 + 初始管理员 admin/随机密码） ---
    AUTO_SEED = _bool("AUTO_SEED", "1")

    # --- 邮件（账户恢复；未配置 MAIL_HOST 时邮箱恢复自动停用） ---
    MAIL_HOST = os.environ.get("MAIL_HOST", "")
    MAIL_PORT = int(os.environ.get("MAIL_PORT", "587"))
    MAIL_USER = os.environ.get("MAIL_USER", "")
    MAIL_PASSWORD = os.environ.get("MAIL_PASSWORD", "")
    MAIL_FROM = os.environ.get("MAIL_FROM", "") or os.environ.get("MAIL_USER", "")
    MAIL_USE_TLS = _bool("MAIL_USE_TLS", "1")

    # --- 账户恢复 ---
    RECOVER_TOKEN_MINUTES = int(os.environ.get("RECOVER_TOKEN_MINUTES", "30"))
    # 删除数据库重启时的恢复模板（manage.py save-default 生成；留空 = DB 同目录 default.db）
    DB_TEMPLATE_PATH = os.environ.get("DB_TEMPLATE_PATH", "")

    # --- 会话 / 安全 ---
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("COOKIE_SECURE", "1")  # 生产 HTTPS 下保持开启
    SESSION_COOKIE_NAME = "s1914"
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 14  # 14 天
    WTF_CSRF_SESSION_KEY = "_csrf_token"

    # --- 注册 / 内容限制 ---
    PASSWORD_MIN_LENGTH = 8
    USERNAME_MIN_LENGTH = 2
    USERNAME_MAX_LENGTH = 20
    MAX_COMMENT_LENGTH = 5000
    MAX_ISSUE_BODY_LENGTH = 20000
    MAX_CARD_DESC_LENGTH = 10000

    @staticmethod
    def init_app(app):
        cfg = app.config
        Path(cfg["DB_PATH"]).parent.mkdir(parents=True, exist_ok=True)
        cfg["UPLOAD_DIR"] = Path(cfg["UPLOAD_DIR"])
        (cfg["UPLOAD_DIR"] / "cards").mkdir(parents=True, exist_ok=True)
