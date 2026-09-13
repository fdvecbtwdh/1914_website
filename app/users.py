"""用户公开主页 — 只公开安全字段，绝不暴露邮箱/IP/角色/会话。"""
from flask import Blueprint, abort, render_template, request

from . import db, auth

bp = Blueprint("users", __name__, url_prefix="")

PAGE_SIZE = 20


def _user_or_404(username: str):
    row = db.query(
        "SELECT id, username, role, bio, created_at, is_banned FROM users WHERE username = ?",
        (username,), one=True)
    if row is None:
        abort(404)
    return row


@bp.route("/u/<username>")
def profile(username: str):
    user = _user_or_404(username)
    tab = request.args.get("tab", "cards")
    page = max(1, min(request.args.get("page", 1, type=int), 500))
    offset = (page - 1) * PAGE_SIZE

    if tab == "issues":
        total = db.query("SELECT COUNT(*) AS n FROM issues WHERE author_id = ?",
                         (user["id"],), one=True)["n"]
        items = db.query(
            """SELECT i.*, IFNULL(v.vc,0) AS vote_count FROM issues i
               LEFT JOIN (SELECT target_id, COUNT(*) vc FROM votes WHERE target_type='issue'
                 GROUP BY target_id) v ON v.target_id = i.id
               WHERE i.author_id = ? ORDER BY i.created_at DESC LIMIT ? OFFSET ?""",
            (user["id"], PAGE_SIZE, offset))
    elif tab == "replies":
        # 该用户的全部回复（含回复的回复），并带出原帖位置
        total = db.query(
            "SELECT COUNT(*) AS n FROM comments WHERE author_id = ? AND is_deleted = 0",
            (user["id"],), one=True)["n"]
        items = db.query(
            """SELECT cm.id, cm.body, cm.parent_id, cm.created_at, cm.target_type, cm.target_id,
                      c.name AS card_name, i.title AS issue_title,
                      EXISTS (SELECT 1 FROM notifications n
                              WHERE n.type = 'comment_mention' AND n.comment_id = cm.id) AS is_mention
               FROM comments cm
               LEFT JOIN cards c ON cm.target_type = 'card' AND c.id = cm.target_id
               LEFT JOIN issues i ON cm.target_type = 'issue' AND i.id = cm.target_id
               WHERE cm.author_id = ? AND cm.is_deleted = 0
               ORDER BY cm.created_at DESC, cm.id DESC LIMIT ? OFFSET ?""",
            (user["id"], PAGE_SIZE, offset))
    else:
        tab = "cards"
        total = db.query(
            "SELECT COUNT(*) AS n FROM cards WHERE author_id = ? AND status = 'visible'",
            (user["id"],), one=True)["n"]
        items = db.query(
            """SELECT c.*, u.username AS author_name, IFNULL(v.vc,0) AS vote_count,
               IFNULL(cm.cc,0) AS comment_count FROM cards c
               LEFT JOIN users u ON u.id = c.author_id
               LEFT JOIN (SELECT target_id, COUNT(*) vc FROM votes WHERE target_type='card'
                 GROUP BY target_id) v ON v.target_id = c.id
               LEFT JOIN (SELECT target_id, COUNT(*) cc FROM comments WHERE target_type='card'
                 AND is_deleted=0 GROUP BY target_id) cm ON cm.target_id = c.id
               WHERE c.author_id = ? AND c.status = 'visible'
               ORDER BY c.created_at DESC LIMIT ? OFFSET ?""",
            (user["id"], PAGE_SIZE, offset))

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    # 举报用户弹窗的"关联回复"选项：最近 20 条回复
    reply_options = db.query(
        """SELECT cm.id, cm.body, c.name AS card_name, i.title AS issue_title
           FROM comments cm
           LEFT JOIN cards c ON cm.target_type = 'card' AND c.id = cm.target_id
           LEFT JOIN issues i ON cm.target_type = 'issue' AND i.id = cm.target_id
           WHERE cm.author_id = ? AND cm.is_deleted = 0
           ORDER BY cm.created_at DESC, cm.id DESC LIMIT 20""", (user["id"],))
    stats = {
        "cards": db.query(
            "SELECT COUNT(*) AS n FROM cards WHERE author_id = ? AND status='visible'",
            (user["id"],), one=True)["n"],
        "issues": db.query("SELECT COUNT(*) AS n FROM issues WHERE author_id = ?",
                           (user["id"],), one=True)["n"],
        "comments": db.query(
            "SELECT COUNT(*) AS n FROM comments WHERE author_id = ? AND is_deleted=0",
            (user["id"],), one=True)["n"],
        "votes_received": db.query(
            """SELECT COUNT(*) AS n FROM votes v WHERE v.target_type = 'card' AND v.target_id IN
               (SELECT id FROM cards WHERE author_id = ?)""",
            (user["id"],), one=True)["n"],
    }
    return render_template("users/profile.html", user=user, tab=tab, items=items,
                           total=total, page=page, pages=pages, stats=stats,
                           reply_options=reply_options)


# ---------- 个人设置 ----------

@bp.route("/settings", methods=["GET", "POST"])
@auth.login_required
def settings():
    user = auth.current_user()
    if request.method == "POST":
        auth.check_csrf()
        bio = (request.form.get("bio") or "").strip()[:200]
        email = (request.form.get("email") or "").strip().lower()
        if email and "@" not in email:
            from flask import flash
            flash("邮箱格式不正确", "danger")
        else:
            from flask import flash
            db.execute("UPDATE users SET bio = ?, email = ? WHERE id = ?",
                       (bio, email or None, user["id"]))
            flash("资料已更新", "success")
            return redirect(request.path)
    fresh = db.query("SELECT * FROM users WHERE id = ?", (user["id"],), one=True)
    m = auth.active_mute(fresh)
    return render_template("users/settings.html", profile_user=fresh,
                           mute_active=bool(m),
                           mute_reason=m["reason"] if m else "",
                           mute_until=m["until"] if m else "",
                           mute_remaining=m["remaining_text"] if m else "",
                           ban_active=bool(fresh["is_banned"]),
                           ban_permanent=fresh["ban_until"] is None,
                           ban_reason=fresh["banned_reason"] or "",
                           ban_until=fresh["ban_until"] or "")


@bp.route("/settings/password", methods=["POST"])
@auth.login_required
def change_password():
    auth.check_csrf()
    from flask import flash, redirect
    user = auth.current_user()
    old = request.form.get("old_password") or ""
    new = request.form.get("new_password") or ""
    confirm = request.form.get("confirm") or ""
    if not auth.verify_password(user["password_hash"], old):
        flash("当前密码错误", "danger")
    elif err := auth.check_password_strength(new):
        flash(err, "danger")
    elif new != confirm:
        flash("两次输入的密码不一致", "danger")
    else:
        db.execute("UPDATE users SET password_hash = ? WHERE id = ?",
                   (auth.hash_password(new), user["id"]))
        auth.revoke_all_sessions(user["id"])
        db.execute("DELETE FROM recovery_tokens WHERE user_id = ? AND used_at IS NULL",
                   (user["id"],))
        auth.create_session(user["id"])
        auth.audit("password_change", "user", user["id"])
        flash("密码已修改，其他设备已强制退出", "success")
    return redirect("/settings")
