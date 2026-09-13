"""管理后台 — 用户 / 卡牌 / Issue / 评论 / 举报 / 标签 / GitHub 同步 / 审计日志。
仅 admin。所有列表页支持筛选；主要列表支持勾选 + 批量操作。
"""
from flask import (Blueprint, abort, flash, make_response, redirect,
                   render_template, request, url_for)

import os
import sqlite3
from datetime import datetime, timedelta

from flask import (Blueprint, abort, current_app, flash, make_response, redirect,
                   render_template, request, url_for)

from . import db, auth
from .gameconstants import (ISSUE_STATUSES, ISSUE_PRIORITIES, CARD_TYPES,
                            RARITIES, UNIT_CLASSES)
from .notify import notify

bp = Blueprint("admin", __name__, url_prefix="/admin")

PAGE_SIZE = 30

USER_ROLES = ("user", "moderator", "admin")
REPORT_TARGETS = ("card", "issue", "comment", "user", "forum_post")


@bp.before_request
def _gate():
    u = auth.current_user()
    if u is None or u["role"] != "admin":
        abort(403)


def _parse_ids() -> list[int]:
    """批量表单的 ids 多选值 → 安全的 int 列表。"""
    out = []
    for v in request.form.getlist("ids"):
        try:
            n = int(v)
        except (TypeError, ValueError):
            continue
        if n > 0:
            out.append(n)
    return out


def _back(endpoint: str, **extra):
    """批量操作后带原筛选参数跳回列表页。"""
    params = {k: v for k, v in request.args.items() if v}
    params.update(extra)
    return redirect(url_for(endpoint, **params))


def _batch_guard(action: str, ids: list, valid: set):
    """校验批量请求；不合法时 flash 并返回 None（调用方直接 redirect）。"""
    if not ids or action not in valid:
        flash("请先勾选要操作的行并选择批量操作", "warning")
        return False
    return True


@bp.route("/")
def index():
    stats = {
        "users": db.query("SELECT COUNT(*) AS n FROM users", one=True)["n"],
        "cards": db.query("SELECT COUNT(*) AS n FROM cards", one=True)["n"],
        "issues": db.query("SELECT COUNT(*) AS n FROM issues", one=True)["n"],
        "comments": db.query("SELECT COUNT(*) AS n FROM comments", one=True)["n"],
        "posts": db.query("SELECT COUNT(*) AS n FROM forum_posts WHERE status='visible'",
                          one=True)["n"],
        "open_reports": db.query("SELECT COUNT(*) AS n FROM reports WHERE status='open'",
                                 one=True)["n"],
        "pending_sync": db.query(
            "SELECT COUNT(*) AS n FROM sync_queue WHERE status='pending'", one=True)["n"],
        "today_users": db.query(
            "SELECT COUNT(*) AS n FROM users WHERE created_at >= date('now')",
            one=True)["n"],
        "today_posts": db.query(
            "SELECT COUNT(*) AS n FROM forum_posts WHERE created_at >= date('now')",
            one=True)["n"],
        "today_comments": db.query(
            "SELECT COUNT(*) AS n FROM comments WHERE created_at >= date('now')",
            one=True)["n"],
        "today_issues": db.query(
            "SELECT COUNT(*) AS n FROM issues WHERE created_at >= date('now')",
            one=True)["n"],
        "muted": db.query(
            "SELECT COUNT(*) AS n FROM users WHERE mute_until IS NOT NULL "
            "AND (mute_until = 'permanent' OR mute_until > datetime('now'))",
            one=True)["n"],
        "temp_banned": db.query(
            "SELECT COUNT(*) AS n FROM users WHERE is_banned=1 AND ban_until IS NOT NULL",
            one=True)["n"],
        "perm_banned": db.query(
            "SELECT COUNT(*) AS n FROM users WHERE is_banned=1 AND ban_until IS NULL",
            one=True)["n"],
        "ip_banned": db.query("SELECT COUNT(*) AS n FROM ip_bans", one=True)["n"],
        "events_24h": db.query(
            "SELECT COUNT(*) AS n FROM security_events "
            "WHERE created_at > datetime('now', '-24 hours')", one=True)["n"],
    }
    recent_audit = db.query(
        """SELECT a.*, u.username AS actor_name FROM audit_log a
           LEFT JOIN users u ON u.id = a.actor_id ORDER BY a.id DESC LIMIT 12""")
    recent_events = db.query(
        "SELECT * FROM security_events ORDER BY id DESC LIMIT 6")
    recent_penalties = db.query(
        """SELECT p.*, u.username AS username, a.username AS admin_name
           FROM penalties p LEFT JOIN users u ON u.id = p.user_id
           LEFT JOIN users a ON a.id = p.created_by
           ORDER BY p.id DESC LIMIT 6""")
    recent_users = db.query(
        "SELECT username, created_at FROM users ORDER BY id DESC LIMIT 5")
    return render_template("admin/index.html", stats=stats, recent_audit=recent_audit,
                           recent_events=recent_events, recent_penalties=recent_penalties,
                           recent_users=recent_users)


# ---------- 用户管理 ----------

# ---------- 安全防护 ----------

@bp.route("/security")
def security_page():
    from . import security
    now = datetime.utcnow()
    bans = []
    for row in db.query("SELECT * FROM ip_bans ORDER BY created_at DESC LIMIT 100"):
        d = dict(row)
        if d["expires_at"]:
            try:
                left = int((datetime.strptime(d["expires_at"][:19],
                            "%Y-%m-%d %H:%M:%S") - now).total_seconds())
            except ValueError:
                left = 0
            d["permanent"] = d["expires_at"] is not None and "permanent" in d["expires_at"]
            d["remaining_text"] = security.remaining_text(max(0, left))
        else:
            d["permanent"] = True
            d["remaining_text"] = None
        d["auto"] = bool(d["auto"])
        bans.append(d)
    stats = {r["kind"]: r["n"] for r in db.query(
        """SELECT kind, COUNT(*) AS n FROM security_events
           WHERE created_at > datetime('now', '-24 hours') GROUP BY kind""")}
    events = [dict(e) for e in db.query(
        "SELECT * FROM security_events ORDER BY id DESC LIMIT 20")]
    return render_template("admin/security.html", bans=bans, stats=stats,
                           events=events, total_bans=len(bans))


@bp.route("/security/ban", methods=["POST"])
def security_ban():
    from . import security
    auth.check_csrf()
    me = auth.current_user()
    ip = (request.form.get("ip") or "").strip().lower()
    reason = (request.form.get("reason") or "").strip() or "管理员手动封禁"
    permanent = request.form.get("permanent") == "1"
    hours = None if permanent else max(1, request.form.get("hours", type=int) or 1)
    security.ban_ip(ip, reason, hours=hours, auto=False, created_by=me["id"])
    auth.audit("ip_ban", detail=f"{ip} {reason}（{hours or '永久'}小时）")
    flash(f"已封禁 IP {ip}", "success")
    return redirect(url_for("admin.security_page"))


@bp.route("/security/unban", methods=["POST"])
def security_unban():
    from . import security
    auth.check_csrf()
    me = auth.current_user()
    ip = (request.form.get("ip") or "").strip().lower()
    if security.unban_ip(ip, actor=me["id"]):
        auth.audit("ip_unban", detail=ip)
        flash(f"已解除 IP {ip} 的封禁", "success")
    else:
        flash("该 IP 当前不在封禁列表中", "warning")
    return redirect(url_for("admin.security_page"))


@bp.route("/users")
def users():
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PAGE_SIZE
    q = (request.args.get("q") or "").strip()
    role = request.args.get("role", "")
    status = request.args.get("status", "")
    where, args = ["1=1"], []
    if q:
        where.append("username LIKE ?")
        args.append(f"%{q}%")
    if role in USER_ROLES:
        where.append("role = ?")
        args.append(role)
    if status == "banned":
        where.append("is_banned = 1")
    elif status == "active":
        where.append("is_banned = 0")
    where_sql = " AND ".join(where)
    total = db.query(f"SELECT COUNT(*) AS n FROM users WHERE {where_sql}",
                     args, one=True)["n"]
    rows = db.query(
        f"""SELECT u.*,
           (u.mute_until IS NOT NULL AND (u.mute_until = 'permanent'
              OR u.mute_until > datetime('now'))) AS mute_active,
           (SELECT COUNT(*) FROM cards c WHERE c.author_id = u.id) AS card_count,
           (SELECT COUNT(*) FROM issues i WHERE i.author_id = u.id) AS issue_count
           FROM users u WHERE {where_sql} ORDER BY u.id DESC LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, offset))
    return render_template("admin/users.html", users=rows, total=total, page=page,
                           pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           q=q, role=role, status=status)


@bp.route("/users/<int:user_id>/mute", methods=["POST"])
def user_mute(user_id: int):
    auth.check_csrf()
    me = auth.current_user()
    if user_id == me["id"]:
        flash("不能对自己执行管理操作", "warning")
        return _back("admin.users")
    target = db.query("SELECT id, username FROM users WHERE id = ?", (user_id,), one=True)
    if target is None:
        abort(404)
    reason = (request.form.get("reason") or "").strip()
    if not reason:
        flash("禁言必须填写原因", "danger")
        return _back("admin.users")
    label = _apply_mute(user_id, reason,
                        request.form.get("permanent") == "1",
                        request.form.get("duration_value", type=int) or 0,
                        request.form.get("duration_unit", "day"), me["id"])
    auth.audit("user_mute", "user", user_id,
               f"{target['username']} {label} 原因：{reason}")
    flash(f"已禁言 {target['username']}（{label}）", "success")
    return _back("admin.users")


@bp.route("/users/<int:user_id>/unmute", methods=["POST"])
def user_unmute(user_id: int):
    auth.check_csrf()
    me = auth.current_user()
    target = db.query("SELECT id, username FROM users WHERE id = ?", (user_id,), one=True)
    if target is None:
        abort(404)
    db.execute("UPDATE users SET mute_until = NULL, mute_reason = NULL WHERE id = ?",
               (user_id,))
    db.execute("UPDATE penalties SET lifted_at = datetime('now'), lifted_by = ? "
               "WHERE user_id = ? AND type = 'mute' AND lifted_at IS NULL",
               (me["id"], user_id))
    auth.audit("user_unmute", "user", user_id, target["username"])
    notify(user_id, me["id"], "user_unmute",
           detail="你的禁言已被管理员解除，现在可以正常发言。",
           dedup_anchor=f"unmute{user_id}")
    flash(f"已解除 {target['username']} 的禁言", "success")
    return _back("admin.users")


# ---------- 系统维护 / 备份 ----------

@bp.route("/system")
def system_page():
    db_path = current_app.config["DB_PATH"]
    upload_dir = current_app.config["UPLOAD_DIR"]
    try:
        db_size = os.path.getsize(db_path)
    except OSError:
        db_size = 0
    upload_bytes = 0
    upload_count = 0
    for root, _dirs, files in os.walk(str(upload_dir)):
        for f in files:
            try:
                upload_bytes += os.path.getsize(os.path.join(root, f))
                upload_count += 1
            except OSError:
                pass
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    backups = []
    if os.path.isdir(backup_dir):
        for f in sorted(os.listdir(backup_dir), reverse=True):
            if f.endswith(".db"):
                fp = os.path.join(backup_dir, f)
                backups.append({"name": f, "size": os.path.getsize(fp),
                                "mtime": datetime.fromtimestamp(os.path.getmtime(fp))})
    return render_template("admin/system.html",
                           db_path=db_path, db_size=db_size,
                           upload_count=upload_count, upload_bytes=upload_bytes,
                           backups=backups[:10], backup_count=len(backups))


@bp.route("/system/backup", methods=["POST"])
def system_backup():
    auth.check_csrf()
    me = auth.current_user()
    db_path = current_app.config["DB_PATH"]
    backup_dir = os.path.join(os.path.dirname(db_path), "backups")
    os.makedirs(backup_dir, exist_ok=True)
    name = f"1914-backup-{datetime.now():%Y%m%d-%H%M%S}.db"
    dest = os.path.join(backup_dir, name)
    src = sqlite3.connect(db_path)
    dst = sqlite3.connect(dest)
    src.backup(dst)
    dst.close()
    src.close()
    # 保留最近 10 份
    files = sorted(f for f in os.listdir(backup_dir) if f.endswith(".db"))
    for old in files[:-10]:
        try:
            os.remove(os.path.join(backup_dir, old))
        except OSError:
            pass
    auth.audit("db_backup", detail=name)
    flash(f"备份完成：{name}", "success")
    return redirect(url_for("admin.system_page"))


@bp.route("/users/<int:user_id>")
def user_detail(user_id: int):
    user = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if user is None:
        abort(404)
    d = dict(user)
    if d.get("mute_until") and d["mute_until"] != "permanent":
        from .auth import active_mute
        m = active_mute(user)
        d["mute_active"] = bool(m)
        d["mute_remaining"] = m["remaining_text"] if m else "已到期"
    penalties = db.query(
        """SELECT p.*, a.username AS admin_name FROM penalties p
           LEFT JOIN users a ON a.id = p.created_by
           WHERE p.user_id = ? ORDER BY p.id DESC LIMIT 20""", (user_id,))
    reported = db.query(
        """SELECT r.*, u.username AS reporter_name FROM reports r
           LEFT JOIN users u ON u.id = r.reporter_id
           WHERE r.target_type = 'user' AND r.target_id = ?
           ORDER BY r.id DESC LIMIT 20""", (user_id,))
    audits = db.query(
        """SELECT a.*, u.username AS actor_name FROM audit_log a
           LEFT JOIN users u ON u.id = a.actor_id
           WHERE a.actor_id = ? OR a.target_id = ?
           ORDER BY a.id DESC LIMIT 20""", (user_id, user_id))
    stats = {
        "posts": db.query(
            "SELECT COUNT(*) AS n FROM forum_posts WHERE author_id = ?", (user_id,),
            one=True)["n"],
        "comments": db.query(
            "SELECT COUNT(*) AS n FROM comments WHERE author_id = ? AND is_deleted = 0",
            (user_id,), one=True)["n"],
        "issues": db.query(
            "SELECT COUNT(*) AS n FROM issues WHERE author_id = ?", (user_id,),
            one=True)["n"],
        "cards": db.query(
            "SELECT COUNT(*) AS n FROM cards WHERE author_id = ?", (user_id,),
            one=True)["n"],
        "reports_filed": db.query(
            "SELECT COUNT(*) AS n FROM reports WHERE reporter_id = ?", (user_id,),
            one=True)["n"],
    }
    return render_template("admin/user_detail.html", u=d, penalties=penalties,
                           reported=reported, audits=audits, stats=stats)


@bp.route("/users/batch", methods=["POST"])
def users_batch():
    auth.check_csrf()
    ids = _parse_ids()
    action = request.form.get("action", "")
    if not _batch_guard(action, ids, {"ban", "unban", "set_user", "set_moderator",
                                      "set_admin", "delete"}):
        return _back("admin.users")
    batch_reason = (request.form.get("batch_reason") or "").strip()
    if action == "ban" and not batch_reason:
        flash("批量封禁必须填写原因", "danger")
        return _back("admin.users")
    me = auth.current_user()
    done = skipped = 0
    for uid in ids:
        if uid == me["id"]:
            skipped += 1  # 不能对自己批量操作
            continue
        target = db.query("SELECT id, username FROM users WHERE id = ?", (uid,), one=True)
        if target is None:
            skipped += 1
            continue
        if action == "ban":
            _apply_ban(uid, batch_reason, True, 0, "day", me["id"])
        elif action == "unban":
            db.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (uid,))
        elif action == "delete":
            db.execute("DELETE FROM users WHERE id = ?", (uid,))  # 内容保留，作者置空
        else:
            role = action.split("_", 1)[1]
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
            auth.revoke_all_sessions(uid)  # 角色变更后重新登录生效
        auth.audit(f"user_{action}", "user", uid, target["username"])
        done += 1
    label = {"ban": "封禁", "unban": "解封", "delete": "删除",
             "set_user": "设为 user", "set_moderator": "设为 moderator",
             "set_admin": "设为 admin"}[action]
    flash(f"批量操作完成：{label} {done}，跳过 {skipped}", "success")
    return _back("admin.users")


DUR_UNITS = {"hour": ("小时", 3600), "day": ("天", 86400), "week": ("周", 604800)}


def _duration_label(permanent: bool, value: int, unit: str) -> str:
    if permanent:
        return "永久"
    unit_label = DUR_UNITS.get(unit, ("天", 86400))[0]
    return f"{value} {unit_label}"


def _apply_mute(uid: int, reason: str, permanent: bool, value: int, unit: str,
                actor: int) -> str:
    """写入禁言状态 + 处罚记录（新处罚覆盖旧禁言并解除旧记录）。返回时长描述。"""
    label = _duration_label(permanent, value, unit)
    if permanent:
        until, expires, flag = "permanent", None, 1
    else:
        seconds = max(1, value) * DUR_UNITS.get(unit, ("", 86400))[1]
        until = (datetime.utcnow() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
        expires, flag = until, 0
    db.execute("UPDATE users SET mute_until=?, mute_reason=? WHERE id=?",
               (until, reason, uid))
    penalty = db.execute(
        """INSERT INTO penalties (user_id, type, reason, permanent, expires_at, created_by)
           VALUES (?,?,?,?,?,?)""", (uid, "mute", reason, flag, expires, actor))
    db.execute("UPDATE penalties SET lifted_at=datetime('now'), lifted_by=? "
               "WHERE user_id=? AND type='mute' AND id != ? AND lifted_at IS NULL",
               (actor, uid, penalty))
    detail = (f"你已被永久禁言。原因：{reason}" if permanent else
              f"你已被禁言 {label}。原因：{reason}。解除时间：{until}。")
    notify(uid, actor, "user_mute", detail=detail, dedup_anchor=f"p{penalty}")
    return label


def _apply_ban(uid: int, reason: str, permanent: bool, value: int, unit: str,
               actor: int) -> str:
    """写入封禁状态 + 处罚记录（临时封禁到期后登录时自动解封）。返回时长描述。"""
    label = _duration_label(permanent, value, unit)
    if permanent:
        ban_until, expires, flag = None, None, 1
    else:
        seconds = max(1, value) * DUR_UNITS.get(unit, ("", 86400))[1]
        ban_until = (datetime.utcnow() + timedelta(seconds=seconds)).strftime("%Y-%m-%d %H:%M:%S")
        expires, flag = ban_until, 0
    db.execute("UPDATE users SET is_banned=1, ban_until=?, banned_reason=? WHERE id=?",
               (ban_until, reason, uid))
    auth.revoke_all_sessions(uid)
    penalty = db.execute(
        """INSERT INTO penalties (user_id, type, reason, permanent, expires_at, created_by)
           VALUES (?,?,?,?,?,?)""", (uid, "ban", reason, flag, expires, actor))
    db.execute("UPDATE penalties SET lifted_at=datetime('now'), lifted_by=? "
               "WHERE user_id=? AND type='ban' AND id != ? AND lifted_at IS NULL",
               (actor, uid, penalty))
    detail = (f"你的账号已被封禁。原因：{reason}" if permanent else
              f"你的账号已被临时封禁 {label}。原因：{reason}。解封时间：{ban_until}。")
    notify(uid, actor, "user_ban", detail=detail, dedup_anchor=f"p{penalty}")
    return label


@bp.route("/users/<int:user_id>/action", methods=["POST"])
def user_action(user_id: int):
    auth.check_csrf()
    me = auth.current_user()
    if user_id == me["id"]:
        flash("不能对自己执行管理操作", "warning")
        return redirect(url_for("admin.users"))
    target = db.query("SELECT * FROM users WHERE id = ?", (user_id,), one=True)
    if target is None:
        abort(404)
    action = request.form.get("action", "")
    if action == "ban":
        reason = (request.form.get("reason") or "").strip()
        if not reason:
            flash("封禁必须填写原因", "danger")
            return redirect(url_for("admin.users"))
        permanent = request.form.get("permanent") == "1"
        value = request.form.get("duration_value", type=int) or 0
        unit = request.form.get("duration_unit", "day")
        label = _apply_ban(user_id, reason, permanent, value, unit, me["id"])
        auth.audit("user_ban", "user", user_id,
                   f"{target['username']} {label} 原因：{reason}")
        flash(f"已封禁 {target['username']}（{label}）", "success")
    elif action == "unban":
        db.execute("UPDATE users SET is_banned = 0, ban_until = NULL, "
                   "banned_reason = NULL WHERE id = ?", (user_id,))
        db.execute("UPDATE penalties SET lifted_at = datetime('now'), lifted_by = ? "
                   "WHERE user_id = ? AND type = 'ban' AND lifted_at IS NULL",
                   (me["id"], user_id))
        notify(user_id, me["id"], "user_unban",
               detail="你的账号已被解封，现在可以正常发言。",
               dedup_anchor=f"unban{user_id}")
        auth.audit("user_unban", "user", user_id, target["username"])
        flash(f"已解封 {target['username']}", "success")
    elif action == "delete":
        db.execute("DELETE FROM users WHERE id = ?", (user_id,))  # 投稿内容保留，作者显示为已注销
        auth.audit("user_delete", "user", user_id, target["username"])
        flash(f"已删除用户 {target['username']}（其投稿内容保留，显示为已注销）", "success")
    elif action in ("set_user", "set_moderator", "set_admin"):
        role = action.split("_")[1]
        db.execute("UPDATE users SET role = ? WHERE id = ?", (role, user_id))
        auth.revoke_all_sessions(user_id)  # 角色变更后重新登录生效
        auth.audit("user_role", "user", user_id, f"{target['username']} -> {role}")
        flash(f"已将 {target['username']} 设为 {role}", "success")
    return redirect(url_for("admin.users"))


# ---------- 卡牌管理 ----------

@bp.route("/cards")
def cards():
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PAGE_SIZE
    q = (request.args.get("q") or "").strip()
    source = request.args.get("source", "")
    status = request.args.get("status", "")
    ctype = request.args.get("type", "")
    uclass = request.args.get("class", "")
    rarity = request.args.get("rarity", "")
    # 视图：detailed（带卡面预览）/ compact（仅图标缩略，行更紧凑）
    view_param = request.args.get("view")
    view = view_param if view_param in ("detailed", "compact") else None
    view = view or request.cookies.get("admin_cards_view") or "detailed"
    where, args = ["1=1"], []
    if q:
        where.append("(c.name LIKE ? OR c.game_id LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if source in ("official", "community"):
        where.append("c.source = ?")
        args.append(source)
    if status in ("visible", "hidden"):
        where.append("c.status = ?")
        args.append(status)
    if ctype in CARD_TYPES:
        where.append("c.type = ?")
        args.append(ctype)
    if uclass in UNIT_CLASSES:
        where.append("c.unit_class = ?")
        args.append(uclass)
    if rarity in RARITIES:
        where.append("c.rarity = ?")
        args.append(rarity)
    where_sql = " AND ".join(where)
    total = db.query(f"SELECT COUNT(*) AS n FROM cards c WHERE {where_sql}",
                     args, one=True)["n"]
    rows = db.query(
        f"""SELECT c.*, u.username AS author_name FROM cards c
           LEFT JOIN users u ON u.id = c.author_id WHERE {where_sql}
           ORDER BY c.id DESC LIMIT ? OFFSET ?""", (*args, PAGE_SIZE, offset))
    resp = make_response(render_template(
        "admin/cards.html", cards=rows, total=total, page=page,
        pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
        q=q, source=source, status=status, ctype=ctype,
        uclass=uclass, rarity=rarity, view=view,
        card_types=CARD_TYPES, unit_classes=UNIT_CLASSES,
        rarities=RARITIES))
    if view_param in ("detailed", "compact"):
        resp.set_cookie("admin_cards_view", view, max_age=30 * 86400, samesite="Lax")
    return resp


@bp.route("/cards/batch", methods=["POST"])
def cards_batch():
    auth.check_csrf()
    ids = _parse_ids()
    action = request.form.get("action", "")
    if not _batch_guard(action, ids, {"hide", "show", "delete"}):
        return _back("admin.cards")
    done = 0
    for cid in ids:
        card = db.query("SELECT id, name FROM cards WHERE id = ?", (cid,), one=True)
        if card is None:
            continue
        if action == "delete":
            db.execute("DELETE FROM cards WHERE id = ?", (cid,))
        else:
            db.execute(
                "UPDATE cards SET status = ?, updated_at = datetime('now') WHERE id = ?",
                ("hidden" if action == "hide" else "visible", cid))
        done += 1
    label = {"hide": "隐藏", "show": "恢复显示", "delete": "删除"}[action]
    auth.audit(f"cards_batch_{action}", "card", None, f"ids={ids} 共{done}")
    flash(f"批量操作完成：{label} {done} 张卡牌", "success")
    return _back("admin.cards")


@bp.route("/cards/<int:card_id>/action", methods=["POST"])
def card_action(card_id: int):
    auth.check_csrf()
    card = db.query("SELECT * FROM cards WHERE id = ?", (card_id,), one=True)
    if card is None:
        abort(404)
    action = request.form.get("action", "")
    if action == "hide":
        db.execute("UPDATE cards SET status='hidden', updated_at=datetime('now') WHERE id=?",
                   (card_id,))
        auth.audit("card_hide", "card", card_id, card["name"])
        flash("卡牌已隐藏", "success")
    elif action == "show":
        db.execute("UPDATE cards SET status='visible', updated_at=datetime('now') WHERE id=?",
                   (card_id,))
        auth.audit("card_show", "card", card_id, card["name"])
        flash("卡牌已恢复显示", "success")
    elif action == "delete":
        db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
        auth.audit("card_delete", "card", card_id, card["name"])
        flash("卡牌已删除", "success")
    return redirect(url_for("admin.cards"))


@bp.route("/cards/import", methods=["POST"])
def cards_import():
    auth.check_csrf()
    from .card_sync import import_official_cards
    result = import_official_cards()
    auth.audit("cards_import", detail=str(result))
    flash(f"官方卡牌导入完成：新建 {result['created']}，更新 {result['updated']}", "success")
    return redirect(url_for("admin.cards"))


# ---------- Issue 管理 ----------

@bp.route("/issues")
def issues():
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PAGE_SIZE
    status = request.args.get("status", "")
    q = (request.args.get("q") or "").strip()
    priority = request.args.get("priority", "")
    author = (request.args.get("author") or "").strip()
    where, args = ["1=1"], []
    if status in ISSUE_STATUSES:
        where.append("i.status = ?")
        args.append(status)
    if q:
        where.append("(i.title LIKE ? OR i.body LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    if priority in ISSUE_PRIORITIES:
        where.append("i.priority = ?")
        args.append(priority)
    if author:
        where.append("u.username LIKE ?")
        args.append(f"%{author}%")
    where_sql = " AND ".join(where)
    total = db.query(
        f"""SELECT COUNT(*) AS n FROM issues i LEFT JOIN users u ON u.id = i.author_id
           WHERE {where_sql}""", args, one=True)["n"]
    rows = db.query(
        f"""SELECT i.*, u.username AS author_name FROM issues i
           LEFT JOIN users u ON u.id = i.author_id WHERE {where_sql}
           ORDER BY i.updated_at DESC LIMIT ? OFFSET ?""", (*args, PAGE_SIZE, offset))
    return render_template("admin/issues.html", issues=rows, total=total, page=page,
                           pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           status=status, q=q, priority=priority, author=author,
                           statuses=ISSUE_STATUSES, priorities=ISSUE_PRIORITIES)


@bp.route("/issues/batch", methods=["POST"])
def issues_batch():
    auth.check_csrf()
    ids = _parse_ids()
    action = request.form.get("action", "")
    status_map = {"set_open": "open", "set_in_progress": "in_progress",
                  "set_resolved": "resolved", "set_closed": "closed"}
    if not _batch_guard(action, ids, set(status_map) | {"delete"}):
        return _back("admin.issues")
    done = 0
    for iid in ids:
        if db.query("SELECT id FROM issues WHERE id = ?", (iid,), one=True) is None:
            continue
        if action == "delete":
            db.execute("DELETE FROM issues WHERE id = ?", (iid,))
        else:
            db.execute(
                "UPDATE issues SET status = ?, updated_at = datetime('now') WHERE id = ?",
                (status_map[action], iid))
        done += 1
    auth.audit("issues_batch", "issue", None, f"{action} ids={ids} 共{done}")
    label = "删除" if action == "delete" else f"状态设为 {ISSUE_STATUSES[status_map[action]]}"
    flash(f"批量操作完成：{done} 个 Issue 已{label}", "success")
    return _back("admin.issues")


@bp.route("/issues/<int:issue_id>/action", methods=["POST"])
def issue_action(issue_id: int):
    auth.check_csrf()
    issue = db.query("SELECT * FROM issues WHERE id = ?", (issue_id,), one=True)
    if issue is None:
        abort(404)
    action = request.form.get("action", "")
    if action == "delete":
        db.execute("DELETE FROM issues WHERE id = ?", (issue_id,))
        auth.audit("issue_delete", "issue", issue_id, issue["title"])
        flash("Issue 已删除", "success")
        return redirect(url_for("admin.issues"))
    if action == "status" and request.form.get("status") in ISSUE_STATUSES:
        status = request.form["status"]
        db.execute("UPDATE issues SET status=?, updated_at=datetime('now') WHERE id=?",
                   (status, issue_id))
        auth.audit("issue_status", "issue", issue_id, status)
        flash("状态已更新", "success")
    return redirect(url_for("admin.issues"))


# ---------- 评论管理 ----------

# ---------- 论坛帖子管理 ----------

@bp.route("/forum")
def forum_admin():
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PAGE_SIZE
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "")
    where, args = ["1=1"], []
    if q:
        where.append("(f.title LIKE ? OR f.body LIKE ?)")
        args.extend([f"%{q}%", f"%{q}%"])
    if status in ("visible", "hidden", "deleted"):
        where.append("f.status = ?")
        args.append(status)
    where_sql = " AND ".join(where)
    total = db.query(
        f"SELECT COUNT(*) AS n FROM forum_posts f WHERE {where_sql}",
        args, one=True)["n"]
    rows = db.query(
        f"""SELECT f.*, u.username AS author_name,
           (SELECT COUNT(*) FROM comments cm WHERE cm.target_type = 'forum_post'
            AND cm.target_id = f.id AND cm.is_deleted = 0) AS reply_count
           FROM forum_posts f LEFT JOIN users u ON u.id = f.author_id
           WHERE {where_sql} ORDER BY f.id DESC LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, offset))
    return render_template("admin/forum.html", posts=rows, total=total,
                           page=page, pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           q=q, status=status)


@bp.route("/forum/<int:post_id>/action", methods=["POST"])
def forum_action(post_id: int):
    auth.check_csrf()
    action = request.form.get("action", "")
    post = db.query("SELECT * FROM forum_posts WHERE id = ?", (post_id,), one=True)
    if post is None:
        abort(404)
    if action in ("hide", "restore", "delete"):
        status = {"hide": "hidden", "restore": "visible", "delete": "deleted"}[action]
        db.execute("UPDATE forum_posts SET status = ?, updated_at = datetime('now') "
                   "WHERE id = ?", (status, post_id))
        auth.audit(f"forum_post_{action}", "forum_post", post_id, post["title"])
        flash(f"帖子已{ {'hide': '隐藏', 'restore': '恢复', 'delete': '删除'}[action] }",
              "success")
    return redirect(url_for("admin.forum_admin"))


@bp.route("/reports/penalty", methods=["POST"])
def report_penalty():
    """举报处理页快捷处罚：禁言 / 临时封禁 / 永久封禁，并可联动标记举报已处理。"""
    auth.check_csrf()
    me = auth.current_user()
    target_uid = request.form.get("user_id", type=int) or 0
    report_id = request.form.get("report_id", type=int) or 0
    ptype = request.form.get("ptype", "")
    reason = (request.form.get("reason") or "").strip()
    if target_uid == me["id"]:
        flash("不能对自己执行处罚", "warning")
        return _back("admin.reports")
    target = db.query("SELECT id, username FROM users WHERE id = ?",
                      (target_uid,), one=True)
    if target is None:
        abort(404)
    if not reason:
        flash("处罚必须填写原因", "danger")
        return _back("admin.reports")
    permanent = request.form.get("permanent") == "1"
    value = request.form.get("duration_value", type=int) or 3
    unit = request.form.get("duration_unit", "day")
    if ptype == "mute":
        label = _apply_mute(target_uid, reason, permanent, value, unit, me["id"])
        done = f"已禁言 {target['username']}（{label}）"
    elif ptype == "tempban":
        label = _apply_ban(target_uid, reason, False, value, unit, me["id"])
        done = f"已临时封禁 {target['username']}（{label}）"
    elif ptype == "permban":
        label = _apply_ban(target_uid, reason, True, 0, "day", me["id"])
        done = f"已永久封禁 {target['username']}"
    else:
        flash("未知的处罚类型", "danger")
        return _back("admin.reports")
    if report_id:
        db.execute("UPDATE reports SET status='resolved', handled_by=? WHERE id=?",
                   (me["id"], report_id))
    auth.audit("report_penalty", "user", target_uid,
               f"{done} 原因：{reason}")
    flash(done + "，相关举报已标记处理", "success")
    return _back("admin.reports")


@bp.route("/comments")
def comments():
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PAGE_SIZE
    ttype = request.args.get("target_type", "")
    state = request.args.get("state", "")
    q = (request.args.get("q") or "").strip()
    where, args = ["1=1"], []
    if ttype in ("card", "issue"):
        where.append("c.target_type = ?")
        args.append(ttype)
    if state == "deleted":
        where.append("c.is_deleted = 1")
    elif state == "active":
        where.append("c.is_deleted = 0")
    if q:
        where.append("(c.body LIKE ? OR u.username LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    where_sql = " AND ".join(where)
    total = db.query(
        f"""SELECT COUNT(*) AS n FROM comments c LEFT JOIN users u ON u.id = c.author_id
           WHERE {where_sql}""", args, one=True)["n"]
    rows = db.query(
        f"""SELECT c.*, u.username AS author_name FROM comments c
           LEFT JOIN users u ON u.id = c.author_id WHERE {where_sql}
           ORDER BY c.id DESC LIMIT ? OFFSET ?""", (*args, PAGE_SIZE, offset))
    return render_template("admin/comments.html", comments=rows, total=total, page=page,
                           pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           ttype=ttype, state=state, q=q)


@bp.route("/comments/batch", methods=["POST"])
def comments_batch():
    auth.check_csrf()
    ids = _parse_ids()
    action = request.form.get("action", "")
    if not _batch_guard(action, ids, {"delete", "restore"}):
        return _back("admin.comments")
    done = 0
    for cid in ids:
        if action == "delete":
            db.execute(
                "UPDATE comments SET is_deleted=1, body='', body_html='' WHERE id = ?", (cid,))
        else:
            db.execute("UPDATE comments SET is_deleted=0 WHERE id = ?", (cid,))
        done += 1
    auth.audit(f"comments_batch_{action}", "comment", None, f"ids={ids} 共{done}")
    flash(f"批量操作完成：{'删除' if action == 'delete' else '恢复'} {done} 条评论", "success")
    return _back("admin.comments")


@bp.route("/comments/<int:comment_id>/action", methods=["POST"])
def comment_action(comment_id: int):
    auth.check_csrf()
    action = request.form.get("action", "")
    if action == "delete":
        db.execute("UPDATE comments SET is_deleted=1, body='', body_html='' WHERE id=?",
                   (comment_id,))
        auth.audit("comment_moderate_delete", "comment", comment_id)
        flash("评论已删除", "success")
    elif action == "restore":
        db.execute("UPDATE comments SET is_deleted=0 WHERE id=?", (comment_id,))
        auth.audit("comment_restore", "comment", comment_id)
        flash("评论已恢复", "success")
    return redirect(url_for("admin.comments"))


# ---------- 举报处理 ----------

def _hide_reported_content(target_type: str, target_id: int,
                           reason: str = "", actor: int | None = None) -> None:
    if target_type == "card":
        db.execute("UPDATE cards SET status='hidden' WHERE id = ?", (target_id,))
    elif target_type == "comment":
        db.execute("UPDATE comments SET is_deleted=1, body='', body_html='' WHERE id=?",
                   (target_id,))
    elif target_type == "user":
        reason = reason or "举报处理"
        db.execute("UPDATE users SET is_banned=1, banned_reason=? WHERE id = ?",
                   (reason, target_id))
        auth.revoke_all_sessions(target_id)
        penalty = db.execute(
            """INSERT INTO penalties (user_id, type, reason, permanent, created_by)
               VALUES (?,?,?,?,?)""",
            (target_id, "ban", reason, 1, actor))
        if actor:
            notify(target_id, actor, "user_ban",
                   detail=f"你的账号已被封禁。原因：{reason}。",
                   dedup_anchor=f"p{penalty}")
    elif target_type == "issue":
        db.execute("UPDATE issues SET status='closed', updated_at=datetime('now') "
                   "WHERE id = ?", (target_id,))
    elif target_type == "forum_post":
        db.execute("UPDATE forum_posts SET status = 'hidden', updated_at = datetime('now') WHERE id = ?", (target_id,))


@bp.route("/reports")
def reports():
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PAGE_SIZE
    status = request.args.get("status", "")
    ttype = request.args.get("target_type", "")
    where, args = ["1=1"], []
    if status in ("open", "resolved", "dismissed"):
        where.append("r.status = ?")
        args.append(status)
    if ttype in REPORT_TARGETS:
        where.append("r.target_type = ?")
        args.append(ttype)
    where_sql = " AND ".join(where)
    total = db.query(f"SELECT COUNT(*) AS n FROM reports r WHERE {where_sql}",
                     args, one=True)["n"]
    rows = db.query(
        f"""SELECT r.*, u.username AS reporter_name FROM reports r
           LEFT JOIN users u ON u.id = r.reporter_id WHERE {where_sql}
           ORDER BY r.status = 'open' DESC, r.id DESC LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, offset))
    items = []
    for r in rows:
        item = dict(r)
        item["target"] = _report_target(r["target_type"], r["target_id"])
        items.append(item)
    return render_template("admin/reports.html", reports=items, total=total, page=page,
                           pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           status=status, ttype=ttype)


@bp.route("/reports/batch", methods=["POST"])
def reports_batch():
    auth.check_csrf()
    ids = _parse_ids()
    action = request.form.get("action", "")
    if not _batch_guard(action, ids, {"resolve", "dismiss", "hide_content"}):
        return _back("admin.reports")
    me = auth.current_user()
    done = 0
    for rid in ids:
        report = db.query("SELECT * FROM reports WHERE id = ?", (rid,), one=True)
        if report is None:
            continue
        if action == "resolve":
            db.execute("UPDATE reports SET status='resolved', handled_by=? WHERE id=?",
                       (me["id"], rid))
        elif action == "dismiss":
            db.execute("UPDATE reports SET status='dismissed', handled_by=? WHERE id=?",
                       (me["id"], rid))
        else:
            _hide_reported_content(report["target_type"], report["target_id"],
                                   report["reason"], me["id"])
            db.execute("UPDATE reports SET status='resolved', handled_by=? WHERE id=?",
                       (me["id"], rid))
        auth.audit(f"report_batch_{action}", report["target_type"], report["target_id"])
        done += 1
    label = {"resolve": "标记已处理", "dismiss": "忽略", "hide_content": "隐藏内容并处理"}[action]
    flash(f"批量操作完成：{label} {done} 条举报", "success")
    return _back("admin.reports")


def _report_target(target_type: str, target_id: int) -> dict | None:
    """举报目标信息。author_id/author_name 供后台"禁言作者"快捷操作。"""
    author_join = " LEFT JOIN users au ON au.id = x.author_id"
    if target_type == "forum_post":
        row = db.query(
            """SELECT x.title, x.author_id, au.username AS author_name
               FROM forum_posts x LEFT JOIN users au ON au.id = x.author_id
               WHERE x.id = ?""", (target_id,), one=True)
        if row is None:
            return None
        return {"label": f"帖子：{row['title']}", "url": f"/forum/{target_id}",
                "author_id": row["author_id"], "author_name": row["author_name"]}
    if target_type == "card":
        row = db.query(
            """SELECT x.name, x.author_id, au.username AS author_name
               FROM cards x LEFT JOIN users au ON au.id = x.author_id
               WHERE x.id = ?""", (target_id,), one=True)
        if row is None:
            return None
        return {"label": f"卡牌：{row['name']}", "url": f"/card/{target_id}",
                "author_id": row["author_id"], "author_name": row["author_name"]}
    if target_type == "issue":
        row = db.query(
            """SELECT x.title, x.author_id, au.username AS author_name
               FROM issues x LEFT JOIN users au ON au.id = x.author_id
               WHERE x.id = ?""", (target_id,), one=True)
        if row is None:
            return None
        return {"label": f"Issue：{row['title']}", "url": f"/issue/{target_id}",
                "author_id": row["author_id"], "author_name": row["author_name"]}
    if target_type == "comment":
        row = db.query(
            """SELECT x.id, x.target_type, x.target_id, x.body, x.author_id,
                      au.username AS author_name
               FROM comments x LEFT JOIN users au ON au.id = x.author_id
               WHERE x.id = ?""", (target_id,), one=True)
        if row is None:
            return None
        url = f"/{'card' if row['target_type'] == 'card' else ('issue' if row['target_type'] == 'issue' else 'forum')}/{row['target_id']}"
        return {"url": url, "label": f"评论：{(row['body'] or '(已删除)')[:60]}",
                "author_id": row["author_id"], "author_name": row["author_name"]}
    if target_type == "user":
        row = db.query("SELECT id, username FROM users WHERE id = ?", (target_id,), one=True)
        if row is None:
            return None
        return {"label": f"用户：{row['username']}", "url": f"/u/{row['username']}",
                "author_id": row["id"], "author_name": row["username"]}
    return None


@bp.route("/reports/<int:report_id>/action", methods=["POST"])
def report_action(report_id: int):
    auth.check_csrf()
    report = db.query("SELECT * FROM reports WHERE id = ?", (report_id,), one=True)
    if report is None:
        abort(404)
    me = auth.current_user()
    action = request.form.get("action", "")
    if action == "resolve":
        db.execute("UPDATE reports SET status='resolved', handled_by=? WHERE id=?",
                   (me["id"], report_id))
        auth.audit("report_resolve", report["target_type"], report["target_id"])
        flash("举报已处理（内容已处理）", "success")
    elif action == "dismiss":
        db.execute("UPDATE reports SET status='dismissed', handled_by=? WHERE id=?",
                   (me["id"], report_id))
        auth.audit("report_dismiss", report["target_type"], report["target_id"])
        flash("举报已忽略", "success")
    elif action == "hide_content":
        _hide_reported_content(report["target_type"], report["target_id"])
        db.execute("UPDATE reports SET status='resolved', handled_by=? WHERE id=?",
                   (me["id"], report_id))
        auth.audit("report_hide_content", report["target_type"], report["target_id"])
        flash("目标内容已隐藏/封禁", "success")
    return redirect(url_for("admin.reports"))


# ---------- 标签管理 ----------

@bp.route("/labels", methods=["GET", "POST"])
def labels():
    if request.method == "POST":
        auth.check_csrf()
        name = (request.form.get("name") or "").strip()[:30]
        color = request.form.get("color", "#6b7a4f").strip()
        if name and db.query("SELECT 1 FROM labels WHERE name = ?", (name,), one=True) is None:
            db.execute("INSERT INTO labels (name, color) VALUES (?,?)", (name, color))
            auth.audit("label_create", detail=name)
            flash("标签已创建", "success")
        elif name:
            db.execute("UPDATE labels SET color = ? WHERE name = ?", (color, name))
            flash("标签颜色已更新", "success")
        return redirect(url_for("admin.labels"))
    rows = db.query(
        """SELECT l.*, (SELECT COUNT(*) FROM content_labels cl WHERE cl.label_id = l.id) AS used
           FROM labels l ORDER BY l.name""")
    return render_template("admin/labels.html", labels=rows)


@bp.route("/labels/batch", methods=["POST"])
def labels_batch():
    auth.check_csrf()
    ids = _parse_ids()
    action = request.form.get("action", "")
    if not _batch_guard(action, ids, {"delete"}):
        return redirect(url_for("admin.labels"))
    for lid in ids:
        db.execute("DELETE FROM labels WHERE id = ?", (lid,))
    auth.audit("labels_batch_delete", "label", None, f"ids={ids}")
    flash(f"批量操作完成：删除 {len(ids)} 个标签", "success")
    return redirect(url_for("admin.labels"))


@bp.route("/labels/<int:label_id>/delete", methods=["POST"])
def label_delete(label_id: int):
    auth.check_csrf()
    db.execute("DELETE FROM labels WHERE id = ?", (label_id,))
    auth.audit("label_delete", "label", label_id)
    flash("标签已删除", "success")
    return redirect(url_for("admin.labels"))


# ---------- GitHub 同步 ----------

@bp.route("/github")
def github():
    qstatus = request.args.get("status", "")
    where, args = "", []
    if qstatus in ("pending", "done", "failed", "skipped"):
        where = "WHERE q.status = ?"
        args = [qstatus]
    queue = db.query(
        f"""SELECT q.*, i.title FROM sync_queue q LEFT JOIN issues i ON i.id = q.issue_id
           {where} ORDER BY q.id DESC LIMIT 50""", args)
    token_set = bool(db.query("SELECT 1") is not None) and bool(
        __import__("flask").current_app.config["GITHUB_TOKEN"])
    return render_template("admin/github.html", queue=queue, token_set=token_set,
                           repo=__import__("flask").current_app.config["GITHUB_REPO"],
                           qstatus=qstatus)


@bp.route("/github/retry", methods=["POST"])
def github_retry():
    auth.check_csrf()
    db.execute("UPDATE sync_queue SET status='pending', attempts=0 WHERE status='failed'")
    flash("失败任务已重置为待处理", "success")
    return redirect(url_for("admin.github"))


# ---------- 审计日志 ----------

@bp.route("/audit")
def audit_log():
    page = max(1, request.args.get("page", 1, type=int))
    offset = (page - 1) * PAGE_SIZE
    action_q = (request.args.get("action") or "").strip()
    q = (request.args.get("q") or "").strip()
    where, args = ["1=1"], []
    if action_q:
        where.append("a.action LIKE ?")
        args.append(f"%{action_q}%")
    if q:
        where.append("(a.detail LIKE ? OR u.username LIKE ?)")
        args += [f"%{q}%", f"%{q}%"]
    where_sql = " AND ".join(where)
    total = db.query(
        f"""SELECT COUNT(*) AS n FROM audit_log a LEFT JOIN users u ON u.id = a.actor_id
           WHERE {where_sql}""", args, one=True)["n"]
    rows = db.query(
        f"""SELECT a.*, u.username AS actor_name FROM audit_log a
           LEFT JOIN users u ON u.id = a.actor_id WHERE {where_sql}
           ORDER BY a.id DESC LIMIT ? OFFSET ?""", (*args, PAGE_SIZE, offset))
    return render_template("admin/audit.html", logs=rows, total=total, page=page,
                           pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           action_q=action_q, q=q)
