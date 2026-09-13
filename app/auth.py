"""账号系统 — 密码哈希(Argon2)、数据库会话、CSRF、登录限速、角色控制。
会话令牌存数据库（可服务端吊销），客户端只持有 opaque token（HttpOnly cookie）。
"""
import functools
import hashlib
import hmac
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


# ---------- 安全问题 ----------

def hash_answer(answer: str) -> str:
    """安全问题答案不以明文存储：归一化后用 SECRET_KEY 派生 HMAC-SHA256。"""
    normalized = re.sub(r"\s+", " ", (answer or "").strip().lower())
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.new(key, normalized.encode(), hashlib.sha256).hexdigest()


def check_answer(answer: str, hashed: str) -> bool:
    if not hashed:
        return False
    normalized = re.sub(r"\s+", " ", (answer or "").strip().lower())
    key = current_app.config["SECRET_KEY"].encode()
    return hmac.compare_digest(
        hmac.new(key, normalized.encode(), hashlib.sha256).hexdigest(), hashed)


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


def remaining_text(seconds: int) -> str:
    """剩余时间的友好格式。"""
    if seconds < 60:
        return "不到 1 分钟"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} 小时 {minutes % 60} 分钟"
    days = seconds // 86400
    return f"{days} 天 {hours % 24} 小时"


def active_mute(user) -> dict | None:
    """禁言状态（服务器时间判断，到期自动视为解除）。
    user 需含 mute_until/mute_reason 字段；未禁言/已到期返回 None。
    """
    until = user["mute_until"] if user is not None else None
    if not until:
        return None
    reason = user["mute_reason"] or "未填写"
    if until == "permanent":
        return {"permanent": True, "reason": reason, "until": None, "remaining_text": None}
    try:
        exp = datetime.strptime(until[:19], "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None
    left = int((exp - _utcnow()).total_seconds())
    if left <= 0:
        return None
    return {"permanent": False, "reason": reason, "until": until,
            "remaining_text": remaining_text(left)}


def mute_notice(mute: dict) -> str:
    """给被禁言用户的完整提示文案（含原因与解除时间）。"""
    if mute["permanent"]:
        return f"你已被永久禁言。原因：{mute['reason']}"
    return (f"你目前处于禁言状态，{mute['remaining_text']}后解除（{mute['until']}）。"
            f"原因：{mute['reason']}")


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
    return f"{kind}:{str(ident).lower()}"

def throttle(kind: str, ident: str, max_hits: int, window_min: int = 15) -> bool:
    """记录一次命中，返回是否已超过窗口内允许次数。"""
    key = _rate_key(kind, ident)
    now_s = datetime.now(timezone.utc).replace(tzinfo=None).strftime("%Y-%m-%d %H:%M:%S")
    row = db.query("SELECT * FROM rate_limits WHERE key = ?", (key,), one=True)
    if row is None:
        db.execute("INSERT INTO rate_limits (key, window_start, fail_count) VALUES (?,?,1)",
                   (key, now_s))
        return False
    start = datetime.strptime(row["window_start"], "%Y-%m-%d %H:%M:%S")
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    if now - start > timedelta(minutes=window_min):
        db.execute("UPDATE rate_limits SET window_start = ?, fail_count = 1 WHERE key = ?",
                   (now_s, key))
        return False
    db.execute("UPDATE rate_limits SET fail_count = fail_count + 1 WHERE key = ?", (key,))
    return row["fail_count"] + 1 > max_hits


def is_throttled(kind: str, ident: str, max_hits: int, window_min: int = 15) -> bool:
    key = _rate_key(kind, ident)
    row = db.query("SELECT * FROM rate_limits WHERE key = ?", (key,), one=True)
    if row is None:
        return False
    start = datetime.strptime(row["window_start"], "%Y-%m-%d %H:%M:%S")
    if datetime.now(timezone.utc).replace(tzinfo=None) - start > timedelta(minutes=window_min):
        return False
    return row["fail_count"] >= max_hits


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

        security_question = (request.form.get("security_question") or "").strip()[:200]
        security_answer = (request.form.get("security_answer") or "").strip()
        err = check_username(username)
        if err:
            flash(err, "danger")
        elif email and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", email):
            flash("邮箱格式不正确", "danger")
        elif err := check_password_strength(password):
            flash(err, "danger")
        elif password != confirm:
            flash("两次输入的密码不一致", "danger")
        elif bool(security_question) != bool(security_answer):
            flash("安全问题和安全问题答案需要同时填写（或同时留空）", "danger")
        elif db.query("SELECT 1 FROM users WHERE username = ?", (username,), one=True):
            flash("用户名已被占用", "danger")
        elif email and db.query("SELECT 1 FROM users WHERE email = ?", (email,), one=True):
            flash("邮箱已被注册", "danger")
        else:
            uid = db.execute(
                """INSERT INTO users (username, email, password_hash,
                   security_question, security_answer_hash) VALUES (?,?,?,?,?)""",
                (username, email or None, hash_password(password),
                 security_question or None,
                 hash_answer(security_answer) if security_answer else None))
            audit("user_register", "user", uid, username)
            create_session(uid)
            flash(f"欢迎加入 1914，{username}！", "success")
            dest = request.args.get("next") or url_for("misc.home")
            return redirect(_safe_next(dest))
    return render_template("auth/register.html",
                           values=request.form if request.method == "POST" else None)


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
            try:
                from . import security
                with current_app.app_context():
                    security.record_event(ip, "login_locked", "blocked",
                                          "多次登录失败，触发 15 分钟锁定")
            except Exception:
                pass
            return render_template("auth/login.html"), 429

        user = db.query(
            "SELECT * FROM users WHERE username = ? OR email = ?", (ident, ident.lower()),
            one=True)
        if user is None:
            register_login_failure("login", ident)
            register_login_failure("login", ip)
            flash("用户名或密码错误", "danger")
            return render_template("auth/login.html", username=ident, password=password)
        if not verify_password(user["password_hash"], password):
            register_login_failure("login", ident)
            register_login_failure("login", ip)
            flash("用户名或密码错误", "danger")
            try:
                from . import security
                with current_app.app_context():
                    level = "blocked" if is_rate_limited(ident, ip) else "info"
                    security.record_event(ip, "login_failed", level,
                                          "登录失败（用户名或密码错误）")
            except Exception:
                pass
            # 保留输入，方便直接改密码重试
            return render_template("auth/login.html", username=ident, password=password)
        if user["is_banned"]:
            # 临时封禁到期：登录时自动解封（无定时任务）
            until = user["ban_until"]
            expired = False
            if until:
                try:
                    expired = datetime.strptime(until[:19], "%Y-%m-%d %H:%M:%S") <= _utcnow()
                except ValueError:
                    expired = False
            if expired:
                db.execute("UPDATE users SET is_banned=0, ban_until=NULL, banned_reason=NULL "
                           "WHERE id=?", (user["id"],))
                audit("ban_auto_expire", "user", user["id"], user["username"])
                user = db.query(
                    "SELECT * FROM users WHERE username = ? OR email = ?",
                    (ident, ident.lower()), one=True)
            else:
                notice = "账号已被封禁" if not until else f"账号已被临时封禁，至 {until[:16]} 解封"
                if user["banned_reason"]:
                    notice += f"。原因：{user['banned_reason']}"
                audit("login_banned", "user", user["id"], user["username"])
                return render_template("auth/login.html", username=ident,
                                       ban_notice=notice), 403
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


# ---------- 账户恢复 ----------

RECOVER_ENTRY_MAX = 20   # 入口尝试 / 15 分钟 / IP
RECOVER_MAIL_MAX = 2     # 邮件发送 / 15 分钟 / 账户
RECOVER_MAIL_IP_MAX = 6  # 邮件发送 / 15 分钟 / IP
RECOVER_Q_MAX = 5        # 安全问题答案错误 / 15 分钟 / 账户
RECOVER_Q_IP_MAX = 10    # 安全问题答案错误 / 15 分钟 / IP


def _mask_email(email: str) -> str:
    name, _, domain = (email or "").partition("@")
    if not domain:
        return ""
    shown = name[:2] if len(name) > 2 else name[:1]
    return f"{shown}***@{domain}"


def _recover_user():
    uid = session.get("recover_uid")
    if not uid:
        return None
    return db.query("SELECT * FROM users WHERE id = ?", (uid,), one=True)


@bp.route("/recover", methods=["GET", "POST"])
def recover():
    if request.method == "POST":
        check_csrf()
        ident = (request.form.get("ident") or "").strip().lstrip("@")
        if throttle("recover_entry", request.remote_addr or "?", RECOVER_ENTRY_MAX, 15):
            flash("尝试过于频繁，请稍后再试。", "warning")
            return render_template("auth/forgot.html")
        user = db.query("SELECT * FROM users WHERE username = ? OR email = ?",
                        (ident, ident.lower()), one=True)
        if user is None or user["is_banned"]:
            flash("无法识别该账户，请检查用户名或邮箱是否正确。", "danger")
            return render_template("auth/forgot.html")
        methods = []
        if user["email"]:
            methods.append("email")
        if user["security_question"]:
            methods.append("question")
        if not methods:
            flash("此账户没有设置可用的恢复方式，因此无法通过此功能恢复密码。", "danger")
            return render_template("auth/forgot.html")
        session["recover_uid"] = user["id"]
        session["recover_methods"] = methods
        return redirect(url_for("auth.recover_methods"))
    return render_template("auth/forgot.html")


@bp.route("/recover/methods")
def recover_methods():
    user = _recover_user()
    if user is None:
        return redirect(url_for("auth.recover"))
    from .emailer import mail_configured
    return render_template("auth/recover_methods.html",
                           recover_user=user,
                           email_masked=_mask_email(user["email"]),
                           mail_ready=mail_configured())


@bp.route("/recover/email", methods=["POST"])
def recover_email():
    user = _recover_user()
    if user is None or not user["email"]:
        return redirect(url_for("auth.recover"))
    ip = request.remote_addr or "?"
    if throttle("recover_mail", str(user["id"]), RECOVER_MAIL_MAX, 15) or        throttle("recover_mail_ip", ip, RECOVER_MAIL_IP_MAX, 15):
        flash("恢复请求过于频繁，请稍后再试。", "warning")
        return redirect(url_for("auth.recover_methods"))
    from .emailer import send_mail, mail_configured
    if not mail_configured():
        flash("邮件功能暂未启用，请联系管理员或改用安全问题恢复。", "warning")
        return redirect(url_for("auth.recover_methods"))
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires = (datetime.now(timezone.utc) + timedelta(
        minutes=current_app.config["RECOVER_TOKEN_MINUTES"])).strftime("%Y-%m-%d %H:%M:%S")
    db.execute("INSERT INTO recovery_tokens (user_id, token_hash, expires_at) VALUES (?,?,?)",
               (user["id"], token_hash, expires))
    link = f"{current_app.config['SITE_URL']}/recover/reset?token={token}"
    minutes = current_app.config["RECOVER_TOKEN_MINUTES"]
    html = (f"<p>你好，</p>"
            f"<p>我们收到了为账户 <b>{user['username']}</b> 重置密码的请求。</p>"
            f"<p>点击下面的链接设置新密码（<b>{minutes} 分钟内有效，且只能使用一次</b>）：</p>"
            f'<p><a href="{link}">{link}</a></p>'
            f"<p>如果这不是你本人的操作，请忽略此邮件，账户不会受影响。</p>")
    if not send_mail(user["email"], "1914.fun 密码恢复", html):
        flash("恢复邮件发送失败，请稍后再试，或改用安全问题恢复。", "danger")
        return redirect(url_for("auth.recover_methods"))
    return redirect(url_for("auth.recover_email_sent"))


@bp.route("/recover/email-sent")
def recover_email_sent():
    if session.get("recover_uid") is None:
        return redirect(url_for("auth.recover"))
    return render_template("auth/mail_sent.html",
                           minutes=current_app.config["RECOVER_TOKEN_MINUTES"])


@bp.route("/recover/question", methods=["GET", "POST"])
def recover_question():
    user = _recover_user()
    if user is None or not user["security_question"]:
        return redirect(url_for("auth.recover"))
    username = user["username"]
    ip = request.remote_addr or "?"
    if is_throttled("recover_q", username, RECOVER_Q_MAX, 15) or        is_throttled("recover_q_ip", ip, RECOVER_Q_IP_MAX, 15):
        session.pop("recover_uid", None)
        flash("安全问题尝试次数过多，该账户的恢复功能已被暂时锁定，请稍后再试。", "danger")
        return redirect(url_for("auth.login"))
    error = None
    if request.method == "POST":
        check_csrf()
        answer = request.form.get("answer", "")
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        if not check_answer(answer, user["security_answer_hash"]):
            register_login_failure("recover_q", username)
            register_login_failure("recover_q", ip)
            error = "安全问题答案不正确。"
        else:
            err = check_password_strength(password)
            if err:
                error = err
            elif password != confirm:
                error = "两次输入的密码不一致"
        if not error:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (hash_password(password), user["id"]))
            db.execute("DELETE FROM recovery_tokens WHERE user_id = ? AND used_at IS NULL",
                       (user["id"],))
            revoke_all_sessions(user["id"])
            clear_login_failures("recover_q", username)
            clear_login_failures("recover_q", ip)
            session.clear()
            flash("密码已重置，请使用新密码登录。", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/recover_question.html",
                           question=user["security_question"], error=error)


@bp.route("/recover/reset", methods=["GET", "POST"])
def recover_reset():
    token = request.values.get("token", "")
    token_hash = hashlib.sha256(token.encode()).hexdigest() if token else ""
    row = None
    if token_hash:
        row = db.query(
            "SELECT * FROM recovery_tokens WHERE token_hash = ? AND used_at IS NULL "
            "AND expires_at > datetime('now')", (token_hash,), one=True)
    if row is None:
        return render_template("auth/recover_reset.html", token=None, invalid=True)
    error = None
    if request.method == "POST":
        auth_check = check_csrf()
        password = request.form.get("password", "")
        confirm = request.form.get("confirm", "")
        err = check_password_strength(password)
        if err:
            error = err
        elif password != confirm:
            error = "两次输入的密码不一致"
        if not error:
            db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                       (hash_password(password), row["user_id"]))
            db.execute("UPDATE recovery_tokens SET used_at = datetime('now') WHERE id = ?",
                       (row["id"],))
            db.execute("DELETE FROM recovery_tokens WHERE user_id = ? AND used_at IS NULL",
                       (row["user_id"],))
            revoke_all_sessions(row["user_id"])
            flash("密码已重置，请使用新密码登录。", "success")
            return redirect(url_for("auth.login"))
    return render_template("auth/recover_reset.html", token=token, error=error)