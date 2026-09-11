"""1914.fun — Flask 应用工厂。"""
import time
from datetime import datetime, timezone

from flask import Flask, request, g

from . import db
from .config import Config
from .auth import bp as auth_bp, current_user, csrf_token, is_moderator
from .markdown_utils import render_markdown, excerpt


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config)
    if test_config:
        app.config.update(test_config)
    Config.init_app(app)

    db.init_db(app.config["DB_PATH"])
    app.teardown_appcontext(db.close_db)
    app.extensions["started_at"] = time.time()

    # ---- 蓝图 ----
    from . import cards as cards_bp_mod
    from . import issues as issues_bp_mod
    from . import users as users_bp_mod
    from . import admin as admin_bp_mod
    from . import misc as misc_bp_mod
    from . import api as api_bp_mod
    from . import nav as nav_bp_mod
    from . import serverstatus as ss_bp_mod

    app.register_blueprint(auth_bp)
    app.register_blueprint(cards_bp_mod.bp)
    app.register_blueprint(issues_bp_mod.bp)
    app.register_blueprint(users_bp_mod.bp)
    app.register_blueprint(admin_bp_mod.bp)
    app.register_blueprint(misc_bp_mod.bp)
    app.register_blueprint(api_bp_mod.bp)
    app.register_blueprint(nav_bp_mod.bp)
    app.register_blueprint(ss_bp_mod.bp)

    # 状态页后台采样线程（网站启动时采样一次，之后每 30 秒更新快照）
    if app.config.get("STATUS_SAMPLER_ENABLED", True):
        ss_bp_mod.start_sampler(app)

    # ---- 模板全局 ----
    from .gameconstants import (RANGES, ABILITIES, NATIONS, UNIT_CLASSES,
                                UNIT_CLASS_ICONS, RARITIES, CARD_TYPES)
    from flask import render_template as _rt

    @app.context_processor
    def inject_globals():
        u = current_user()
        return {
            "current_user": u,
            "csrf_token": csrf_token,
            "is_moderator": is_moderator,
            "site_name": app.config["SITE_NAME"],
            "site_url": app.config["SITE_URL"],
            "site_domain": app.config["SITE_DOMAIN"],
            "site_description": app.config["SITE_DESCRIPTION"],
            "game_version": app.config["GAME_VERSION"],
            "game_version_label": app.config["GAME_VERSION_LABEL"],
            "github_repo_url": app.config["GITHUB_REPO_URL"],
            "site_repo_url": app.config["SITE_REPO_URL"],
            "now": datetime.now(timezone.utc),
            # 游戏领域常量（卡面渲染用）
            "ranges": RANGES,
            "ability_tips": ABILITIES,
            "nations": NATIONS,
            "unit_classes": UNIT_CLASSES,
            "class_icons": UNIT_CLASS_ICONS,
            "rarities": RARITIES,
            "card_types": CARD_TYPES,
        }

    @app.template_filter("md")
    def md_filter(text):
        return render_markdown(text or "")

    @app.template_filter("excerpt")
    def excerpt_filter(text, length=160):
        return excerpt(text, length)

    @app.template_filter("dt")
    def dt_filter(value, fmt="%Y-%m-%d %H:%M"):
        """SQLite UTC 时间字符串 → 本地化显示（按服务器时区）。"""
        if not value:
            return ""
        try:
            dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return str(value)
        return dt.strftime(fmt)

    @app.template_filter("ago")
    def ago_filter(value):
        if not value:
            return ""
        try:
            dt = datetime.strptime(str(value)[:19], "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=timezone.utc)
        except ValueError:
            return str(value)
        secs = (datetime.now(timezone.utc) - dt).total_seconds()
        if secs < 0:
            secs = 0
        for unit, div in (("年", 31536000), ("个月", 2592000), ("天", 86400),
                          ("小时", 3600), ("分钟", 60)):
            n = int(secs // div)
            if n >= 1:
                return f"{n} {unit}前"
        return "刚刚"

    # ---- 安全响应头 ----
    @app.after_request
    def security_headers(resp):
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        if request.path.startswith(("/static/", "/uploads/", "/card-art/")):
            resp.headers.setdefault("Cache-Control", "public, max-age=86400")
        return resp

    # ---- 请求计数（简单防刷：每秒每 IP 最多 30 个请求）----
    _bucket: dict = {}

    @app.before_request
    def simple_throttle():
        ip = request.remote_addr or "?"
        now = time.time()
        window = _bucket.get(ip)
        if window is None or now - window[0] >= 1.0:
            _bucket[ip] = (now, 1)
            # 防止无限增长
            if len(_bucket) > 5000:
                _bucket.clear()
            return None
        count = window[1] + 1
        _bucket[ip] = (window[0], count)
        if count > 30:
            g.request_throttled = True

    # ---- 错误页 ----
    def _error_page(code, message):
        from flask import render_template
        try:
            return render_template("error.html", code=code, message=message), code
        except Exception:
            return f"{code} {message}", code

    @app.errorhandler(400)
    def e400(e):
        return _error_page(400, e.description if hasattr(e, "description") else "请求无效")

    @app.errorhandler(403)
    def e403(e):
        return _error_page(403, "没有权限执行此操作")

    @app.errorhandler(404)
    def e404(e):
        return _error_page(404, "页面不存在")

    @app.errorhandler(413)
    def e413(e):
        return _error_page(413, "文件过大")

    @app.errorhandler(500)
    def e500(e):
        return _error_page(500, "服务器内部错误")

    # ---- GitHub 同步后台线程（可选，失败不影响站点）----
    if app.config["GITHUB_SYNC_ENABLED"]:
        from . import github_sync
        github_sync.start_poller(app)

    return app
