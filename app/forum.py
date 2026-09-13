"""论坛 — 游戏社区讨论区。
帖子存 forum_posts 表；回复完全复用 comments（target_type='forum_post'），
因此评论的编辑/删除/举报/消息通知/Markdown 渲染全部沿用现有机制。
"""
from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from . import db, auth, interactions
from .gameconstants import FORUM_CATEGORIES, DEFAULT_FORUM_CATEGORY

bp = Blueprint("forum", __name__)

PAGE_SIZE = 20


def _post_or_404(post_id: int):
    """可见帖子；版主可看隐藏帖，已删除帖对所有人 404。"""
    row = db.query(
        """SELECT f.*, u.username AS author_name, u.role AS author_role
           FROM forum_posts f LEFT JOIN users u ON u.id = f.author_id
           WHERE f.id = ?""", (post_id,), one=True)
    if row is None or row["status"] == "deleted":
        abort(404)
    if row["status"] != "visible" and not auth.is_moderator():
        abort(404)
    return row


@bp.route("/forum")
def list_posts():
    sort = request.args.get("sort", "latest")
    category = request.args.get("category", "")
    page = max(1, min(request.args.get("page", 1, type=int), 500))

    where, args = ["f.status = 'visible'"], []
    if category in FORUM_CATEGORIES:
        where.append("f.category = ?")
        args.append(category)
    where_sql = " AND ".join(where)
    order = ("COALESCE(r.reply_at, f.created_at) DESC, f.id DESC" if sort == "recent_reply"
             else "f.created_at DESC, f.id DESC")

    base = f"""FROM forum_posts f
        LEFT JOIN users u ON u.id = f.author_id
        LEFT JOIN (SELECT cm.target_id, COUNT(*) rc, MAX(cm.created_at) reply_at,
            (SELECT cu.username FROM comments cm2 JOIN users cu ON cu.id = cm2.author_id
             WHERE cm2.target_type = 'forum_post' AND cm2.target_id = cm.target_id
               AND cm2.is_deleted = 0
             ORDER BY cm2.created_at DESC, cm2.id DESC LIMIT 1) AS last_reply_by
          FROM comments cm WHERE cm.target_type = 'forum_post' AND cm.is_deleted = 0
          GROUP BY cm.target_id) r ON r.target_id = f.id
        WHERE {where_sql}"""
    total = db.query(f"SELECT COUNT(*) AS n {base}", tuple(args), one=True)["n"]
    rows = db.query(
        f"""SELECT f.*, u.username AS author_name, IFNULL(r.rc, 0) AS reply_count,
            r.reply_at, r.last_reply_by {base} ORDER BY {order} LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE))
    posts = [dict(r) for r in rows]

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template("forum/list.html", posts=posts, total=total, page=page,
                           pages=pages, sort=sort, category=category,
                           categories=FORUM_CATEGORIES)


@bp.route("/forum/new", methods=["GET", "POST"])
@auth.login_required
def new_post():
    if request.method == "POST":
        auth.check_csrf()
        user = auth.current_user()
        mute = auth.active_mute(user)
        if mute:
            flash(auth.mute_notice(mute), "danger")
            return redirect(url_for("forum.list_posts"))
        if auth.throttle("sub_forum", user["id"], 3, 1):
            try:
                with current_app.app_context():
                    from . import security
                    security.record_event(security.client_ip(), "submit_limit",
                                          "suspicious", "发帖过于频繁（1 分钟内多帖）")
            except Exception:
                pass
            flash("发帖过于频繁，请稍后再试", "danger")
            return render_template("forum/form.html", categories=FORUM_CATEGORIES,
                                   form_values=request.form, post=None, editing=False), 429
        values = _validate()
        if values is None:
            return render_template("forum/form.html", categories=FORUM_CATEGORIES,
                                   form_values=request.form, post=None, editing=False), 400
        pid = db.execute(
            """INSERT INTO forum_posts (title, body, category, author_id)
               VALUES (?,?,?,?)""",
            (values["title"], values["body"], values["category"],
             auth.current_user()["id"]))
        auth.audit("forum_post_create", "forum_post", pid, values["title"])
        flash("帖子发布成功", "success")
        return redirect(url_for("forum.detail", post_id=pid))
    return render_template("forum/form.html", categories=FORUM_CATEGORIES,
                           form_values=None, post=None, editing=False)


@bp.route("/forum/<int:post_id>")
def detail(post_id):
    post = _post_or_404(post_id)
    comments = interactions.comments_for("forum_post", post_id)
    reply_count = interactions.comment_count("forum_post", post_id)
    return render_template("forum/detail.html", post=post, comments=comments,
                           reply_count=reply_count)


@bp.route("/forum/<int:post_id>/edit", methods=["GET", "POST"])
@auth.login_required
def edit_post(post_id: int):
    post = db.query("SELECT * FROM forum_posts WHERE id = ?", (post_id,), one=True)
    if post is None or post["status"] == "deleted":
        abort(404)
    user = auth.current_user()
    if post["author_id"] != user["id"] and not auth.is_moderator():
        auth.audit("forum_post_edit_denied", "forum_post", post_id)
        abort(403)
    if request.method == "POST":
        auth.check_csrf()
        mute = auth.active_mute(user)
        if mute:
            flash(auth.mute_notice(mute), "danger")
            return redirect(url_for("forum.detail", post_id=post_id))
        values = _validate()
        if values is None:
            return render_template("forum/form.html", categories=FORUM_CATEGORIES,
                                   form_values=request.form, post=post, editing=True), 400
        db.execute(
            """UPDATE forum_posts SET title = ?, body = ?, category = ?,
               updated_at = datetime('now') WHERE id = ?""",
            (values["title"], values["body"], values["category"], post_id))
        auth.audit("forum_post_edit", "forum_post", post_id, values["title"])
        flash("帖子已更新", "success")
        return redirect(url_for("forum.detail", post_id=post_id))
    return render_template("forum/form.html", categories=FORUM_CATEGORIES,
                           form_values=None, post=post, editing=True)


@bp.route("/forum/<int:post_id>/delete", methods=["POST"])
@auth.login_required
def delete_post(post_id: int):
    auth.check_csrf()
    post = db.query("SELECT author_id FROM forum_posts WHERE id = ?", (post_id,), one=True)
    if post is None:
        abort(404)
    user = auth.current_user()
    if post["author_id"] != user["id"] and not auth.is_moderator():
        auth.audit("forum_post_delete_denied", "forum_post", post_id)
        abort(403)
    db.execute("UPDATE forum_posts SET status = 'deleted', updated_at = datetime('now') "
               "WHERE id = ?", (post_id,))
    auth.audit("forum_post_delete", "forum_post", post_id)
    flash("帖子已删除", "success")
    return redirect(url_for("forum.list_posts"))


def _validate():
    """发帖/编辑共用校验。通过返回字段 dict，否则 None（flash 已设置）。"""
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    category = (request.form.get("category") if request.form.get("category") in FORUM_CATEGORIES
                else DEFAULT_FORUM_CATEGORY)
    if not (3 <= len(title) <= 80):
        flash("标题需要 3-80 个字符", "danger")
        return None
    if not body:
        flash("正文不能为空", "danger")
        return None
    return {"title": title, "body": body, "category": category}
