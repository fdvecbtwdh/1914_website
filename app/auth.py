"""账号系统 — 密码哈希(Argon2)、数据库会话、CSRF、登录限速、角色控制。
会话令牌存数据库（可服务端吊销），客户端只持有 opaque token（HttpOnly cookie）。
"""
import functools
import re
import secrets
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, current_app, g, redirect, request, session,
                   url_for, abort, flash)
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, InvalidHashError

from . import db

bp = Blueprint("auth", __name__)

_hasher = PasswordHasher()

MAX_FAILS = 5
FAIL_WINDOW_MIN = 15


def _utcnow() -> datetime:
    """朴素 UTC 时间（与 SQLite datetime('now') 存储格式一致）。"""
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ---------- 密码 ----------

def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except (VerifyMismatchError, InvalidHashError):
        return False


def _cfg(key: str):
    """优先取当前应用配置，CLI 场景回退到 Config 类。"""
    if current_app:
        return current_app.config[key]
    from .config import Config
    return getattr(Config, key)


def check_password_strength(plain: str) -> str | None:
    """返回错误提示，None 表示合格。"""
    if len(plain) < _cfg("PASSWORD_MIN_LENGTH"):
        return f"密码至少 {_cfg('PASSWORD_MIN_LENGTH')} 位"
    if not re.search(r"[A-Za-z]", plain) or not re.search(r"\d", plain):
        return "密码需同时包含字母和数字"
    return None


# ---------- 用户名 ----------

USERNAME_RE = re.compile(r"^[A-Za-z0-9_\-\u4e00-\u9fff]+$")


def check_username(username: str) -> str | None:
    lo, hi = _cfg("USERNAME_MIN_LENGTH"), _cfg("USERNAME_MAX_LENGTH")
    if not (lo <= len(username) <= hi):
        return f"用户名长度需在 {lo}–{hi} 字符之间"
    if not USERNAME_RE.match(username):
        return "用户名只能包含中文、字母、数字、下划线和连字符"
    return None


# ---------- 会话 ----------

def create_session(user_id: int) -> None:
    token = secrets.token_urlsafe(32)
    expires = _utcnow() + timedelta(
        days=current_app.config["PERMANENT_SESSION_LIFETIME"] / 86400)
    db.execute(
        "INSERT INTO sessions (id, user_id, expires_at, ip, user_agent) VALUES (?,?,?,?,?)",
        (token, user_id, expires.strftime("%Y-%m-%d %H:%M:%S"),
         request.remote_addr or "", (request.user_agent.string or "")[:200]),
    )
    session.clear()
    session["sid"] = token
    session["uid"] = user_id
    session.permanent = True


def destroy_session() -> None:
    token = session.get("sid")
    if token:
        db.execute("DELETE FROM sessions WHERE id = ?", (token,))
    session.clear()


def revoke_all_sessions(user_id: int) -> None:
    db.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def _load_current_user() -> None:
    if "current_user" in g:
        return
    g.current_user = None
    sid = session.get("sid")
    uid = session.get("uid")
    if not sid or not uid:
        return
    row = db.query(
        """SELECT u.* FROM sessions s JOIN users u ON u.id = s.user_id
           WHERE s.id = ? AND s.expires_at > datetime('now') AND u.is_banned = 0""",
        (sid,), one=True)
    if row and row["id"] == uid:
        g.current_user = row
    else:
        session.clear()


def current_user():
    _load_current_user()
    return g.current_user


def is_logged_in() -> bool:
    return current_user() is not None


def is_admin() -> bool:
    u = current_user()
    return bool(u and u["role"] == "admin")


def is_moderator() -> bool:
    u = current_user()
    return bool(u and u["role"] in ("moderator", "admin"))


def login_required(view):
    @functools.wraps(view)
    def wrapped(*args, **kwargs):
        if not is_logged_in():
            flash("请先登录", "warning")
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def role_required(*roles):
    def deco(view):
        @functools.wraps(view)
        def wrapped(*args, **kwargs):
            u = current_user()
            if not u:
                flash("请先登录", "warning")
                return redirect(url_for("auth.login", next=request.path))
            if u["role"] not in roles:
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return deco


# ---------- CSRF ----------

def csrf_token() -> str:
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf() -> None:
    token = request.form.get("csrf_token", "") or request.headers.get("X-CSRF-Token", "")
    good = session.get("csrf", "")
    if not good or not token or not secrets.compare_digest(token, good):
        abort(400, description="CSRF 校验失败，请刷新页面重试")


# ---------- 登录限速 ----------

def _rate_key(kind: str, ident: str) -> str:
    return f"{kind}:{ident.lower()}"


def register_login_failure(kind: str, ident: str) -> None:
    key = _rate_key(kind, ident)
    row = db.query("SELECT * FROM rate_limits WHERE key = ?", (key,), one=True)
    now = _utcnow()
    if row is None:
        db.execute("INSERT INTO rate_limits (key, window_start, fail_count) VALUES (?,?,1)",
                   (key, now.strftime("%Y-%m-%d %H:%M:%S")))
        return
    start = datetime.strptime(row["window_start"], "%Y-%m-%d %H:%M:%S")
    if now - start > timedelta(minutes=FAIL_WINDOW_MIN):
        db.execute("UPDATE rate_limits SET window_start = ?, fail_count = 1 WHERE key = ?",
                   (now.strftime("%Y-%m-%d %H:%M:%S"), key))
    else:
        db.execute("UPDATE rate_limits SET fail_count = fail_count + 1 WHERE key = ?", (key,))


def clear_login_failures(kind: str, ident: str) -> None:
    db.execute("DELETE FROM rate_limits WHERE key = ?", (_rate_key(kind, ident),))


def is_rate_limited(*idents: str) -> bool:
    """任一 key 在时间窗内失败次数达到上限即视为被限速。"""
    now = _utcnow()
    for ident in idents:
        if not ident:
            continue
        row = db.query("SELECT * FROM rate_limits WHERE key = ?",
                       (_rate_key("login", ident),), one=True)
        if row is None:
            continue
        start = datetime.strptime(row["window_start"], "%Y-%m-%d %H:%M:%S")
        if now - start <= timedelta(minutes=FAIL_WINDOW_MIN) and row["fail_count"] >= MAX_FAILS:
            return True
    return False


# ---------- 审计 ----------

def audit(action: str, target_type: str = "", target_id=None, detail: str = "") -> None:
    u = current_user()
    db.execute(
        "INSERT INTO audit_log (actor_id, action, target_type, target_id, detail) VALUES (?,?,?,?,?)",
        (u["id"] if u else None, action, target_type, target_id, detail[:500]),
    )


# ---------- 路由 ----------

from flask import render_template


@bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user():
        return redirect(url_for("misc.home"))
    if request.method == "POST":
        check_csrf()
        username = (request.form.get("username") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm") or ""

        err = check_username(username)
        if err:
            flash(err, "danger")
        elif email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("邮箱格式不正确", "danger")
        elif err := check_password_strength(password):
            flash(err, "danger")
        elif password != confirm:
            flash("两次输入的密码不一致", "danger")
        elif db.query("SELECT 1 FROM users WHERE username = ?", (username,), one=True):
            flash("用户名已被占用", "danger")
        elif email and db.query("SELECT 1 FROM users WHERE email = ?", (email,), one=True):
            flash("邮箱已被注册", "danger")
        else:
            uid = db.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (?,?,?)",
                (username, email or None, hash_password(password)))
            audit("user_register", "user", uid, username)
            create_session(uid)
            flash(f"欢迎加入 1914，{username}！", "success")
            dest = request.args.get("next") or url_for("misc.home")
            return redirect(_safe_next(dest))
    return render_template("auth/register.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user():
        return redirect(url_for("misc.home"))
    if request.method == "POST":
        check_csrf()
        ident = (request.form.get("username") or "").strip()
        password = request.form.get("password") or ""
        ip = request.remote_addr or "?"

        if is_rate_limited(ident, ip):
            flash("登录失败次数过多，请 15 分钟后再试", "danger")
            audit("login_rate_limited", detail=ident)
            return render_template("auth/login.html"), 429

        user = db.query(
            "SELECT * FROM users WHERE username = ? OR email = ?", (ident, ident.lower()),
            one=True)
        if user is None or user["is_banned"]:
            register_login_failure("login", ident)
            register_login_failure("login", ip)
            flash("用户名或密码错误", "danger")
            # 保留输入，方便直接改密码重试
            return render_template("auth/login.html", username=ident, password=password)
        elif not verify_password(user["password_hash"], password):
            register_login_failure("login", ident)
            register_login_failure("login", ip)
            flash("用户名或密码错误", "danger")
            return render_template("auth/login.html", username=ident, password=password)
        else:
            clear_login_failures("login", ident)
            clear_login_failures("login", ip)
            db.execute("UPDATE users SET last_login_at = datetime('now') WHERE id = ?",
                       (user["id"],))
            create_session(user["id"])
            audit("login", "user", user["id"], user["username"])
            dest = request.args.get("next") or url_for("misc.home")
            flash(f"欢迎回来，{user['username']}！", "success")
            return redirect(_safe_next(dest))
    return render_template("auth/login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    check_csrf()
    destroy_session()
    flash("已退出登录", "success")
    return redirect(url_for("misc.home"))


def _safe_next(dest: str) -> str:
    """防开放重定向：只允许站内路径。"""
    if dest and dest.startswith("/") and not dest.startswith("//"):
        return dest
    return "/index"
