"""论坛 — 游戏社区讨论区。
帖子存 forum_posts 表；回复完全复用 comments（target_type='forum_post'），
因此评论的编辑/删除/举报/消息通知/Markdown 渲染全部沿用现有机制。

status: visible / hidden / deleted / archived（归档：内容冻结可查看，禁止新互动）
提案板块（卡牌修改提案 / 卡牌转正）的帖子由提案系统自动创建，
复用帖子的评论/回复/举报/排序/管理能力，但不可手动发帖、编辑或删除。
"""
from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from . import db, auth, interactions
from .gameconstants import (FORUM_CATEGORIES, FORUM_BOARDS, FORUM_BOARD_MAP,
                            DEFAULT_FORUM_CATEGORY, POSTABLE_CATEGORIES,
                            ADMIN_ONLY_CATEGORIES, PROPOSAL_CATEGORIES,
                            PROPOSAL_STATUSES)

bp = Blueprint("forum", __name__)

PAGE_SIZE = 20


def _postable_categories_for_user() -> tuple:
    """当前用户可手动发帖的板块：提案板块永远排除，日志板块仅管理员。"""
    if auth.is_admin():
        return POSTABLE_CATEGORIES
    return tuple(c for c in POSTABLE_CATEGORIES if c not in ADMIN_ONLY_CATEGORIES)


def _post_or_404(post_id: int):
    """可见帖子；版主可看隐藏帖；归档帖所有人可查看；已删除帖对所有人 404。"""
    row = db.query(
        """SELECT f.*, u.username AS author_name, u.role AS author_role
           FROM forum_posts f LEFT JOIN users u ON u.id = f.author_id
           WHERE f.id = ?""", (post_id,), one=True)
    if row is None or row["status"] == "deleted":
        abort(404)
    if row["status"] == "hidden" and not auth.is_moderator():
        abort(404)
    return row


def _proposal_for_post(post_id: int) -> dict | None:
    row = db.query("SELECT * FROM card_proposals WHERE forum_post_id = ?",
                   (post_id,), one=True)
    if row is None:
        return None
    from .proposals import proposal_view
    return proposal_view(dict(row))


@bp.route("/forum")
def list_posts():
    """无 category 参数 = 论坛板块首页（板块卡片网格）；
    带 category = 进入板块，复用现有帖子列表 + 筛选。"""
    category = request.args.get("category", "")
    if category not in FORUM_CATEGORIES:
        return _board_home()
    return _board_posts(category)


def _board_home():
    """论坛首页：板块卡片网格（不直接堆帖子列表）。"""
    stats = {r["category"]: r for r in db.query(
        """SELECT category, COUNT(*) AS n, MAX(updated_at) AS last_at
           FROM forum_posts WHERE status IN ('visible','archived')
           GROUP BY category""")}
    boards = []
    for b in FORUM_BOARDS:
        d = dict(b)
        s = stats.get(b["name"])
        d["post_count"] = s["n"] if s else 0
        d["last_at"] = s["last_at"] if s else None
        boards.append(d)
    return render_template("forum/home.html", boards=boards)


def _board_posts(category: str):
    sort = request.args.get("sort", "latest")
    state = request.args.get("state", "")       # ''=未归档 / archived / all
    pstatus = request.args.get("pstatus", "")   # 提案板块：按提案状态筛选
    card_id = request.args.get("card", 0, type=int)
    page = max(1, min(request.args.get("page", 1, type=int), 500))
    board = FORUM_BOARD_MAP[category]
    is_proposal_board = category in PROPOSAL_CATEGORIES

    where, args = ["f.status != 'deleted'", "f.status != 'hidden'"], []
    if is_proposal_board and pstatus in PROPOSAL_STATUSES:
        # 按提案状态筛选：帖子可见性由提案状态决定（批准/未批准 → 归档帖）
        where.append("cp.status = ?")
        args.append(pstatus)
    elif state == "archived":
        where.append("f.status = 'archived'")
    elif state == "all":
        pass
    else:
        where.append("f.status = 'visible'")  # 默认：未归档
    if category in FORUM_CATEGORIES:
        where.append("f.category = ?")
        args.append(category)
    if card_id:
        where.append("cp.card_id = ?")
        args.append(card_id)
    where_sql = " AND ".join(where)
    if card_id:
        # 卡牌相关提案：活跃优先，再按分数，最后时间
        order = ("CASE WHEN cp.status IN ('active','returned') THEN 0 ELSE 1 END, "
                 "proposal_score DESC, f.created_at DESC, f.id DESC")
    elif sort == "score":
        order = "proposal_score DESC, f.created_at DESC, f.id DESC"
    elif sort == "replies":
        order = "reply_count DESC, f.created_at DESC, f.id DESC"
    elif sort == "recent_reply":
        order = "COALESCE(r.reply_at, f.created_at) DESC, f.id DESC"
    else:
        order = "f.created_at DESC, f.id DESC"

    base = f"""FROM forum_posts f
        LEFT JOIN users u ON u.id = f.author_id
        LEFT JOIN card_proposals cp ON cp.forum_post_id = f.id
        LEFT JOIN (SELECT target_id, SUM(direction) sc FROM votes
          WHERE target_type = 'proposal' GROUP BY target_id) pv ON pv.target_id = cp.id
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
        f"""SELECT f.*, u.username AS author_name,
            IFNULL(r.rc, 0) AS reply_count, r.reply_at, r.last_reply_by,
            cp.id AS proposal_id, cp.status AS proposal_status,
            cp.type AS proposal_type, cp.card_id AS proposal_card_id,
            IFNULL(pv.sc, 0) AS proposal_score {base} ORDER BY {order} LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE))
    posts = []
    for r in rows:
        d = dict(r)
        if d.get("proposal_id"):
            d["proposal_status_label"] = PROPOSAL_STATUSES.get(d["proposal_status"],
                                                               d["proposal_status"])
        posts.append(d)

    card_name = None
    if card_id:
        row = db.query("SELECT name FROM cards WHERE id = ?", (card_id,), one=True)
        card_name = row["name"] if row else None

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template("forum/list.html", posts=posts, total=total, page=page,
                           pages=pages, sort=sort, category=category, board=board,
                           is_proposal_board=is_proposal_board, state=state,
                           pstatus=pstatus, proposal_statuses=PROPOSAL_STATUSES,
                           categories=FORUM_CATEGORIES, card_id=card_id,
                           card_name=card_name)


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
            return render_template("forum/form.html", categories=_postable_categories_for_user(),
                                   form_values=request.form, post=None, editing=False), 429
        values = _validate()
        if values is None:
            return render_template("forum/form.html", categories=_postable_categories_for_user(),
                                   form_values=request.form, post=None, editing=False), 400
        pid = db.execute(
            """INSERT INTO forum_posts (title, body, category, author_id)
               VALUES (?,?,?,?)""",
            (values["title"], values["body"], values["category"],
             auth.current_user()["id"]))
        auth.audit("forum_post_create", "forum_post", pid, values["title"])
        flash("帖子发布成功", "success")
        return redirect(url_for("forum.detail", post_id=pid))
    return render_template("forum/form.html", categories=_postable_categories_for_user(),
                           form_values=None, post=None, editing=False)


@bp.route("/forum/<int:post_id>")
def detail(post_id):
    post = _post_or_404(post_id)
    comments = interactions.comments_for("forum_post", post_id)
    reply_count = interactions.comment_count("forum_post", post_id)
    proposal = _proposal_for_post(post_id)
    my_vote = 0
    if proposal and auth.is_logged_in():
        my_vote = interactions.user_vote_direction(auth.current_user()["id"],
                                                   "proposal", proposal["id"])
    return render_template("forum/detail.html", post=post, comments=comments,
                           reply_count=reply_count, proposal=proposal,
                           proposal_my_vote=my_vote)


@bp.route("/forum/<int:post_id>/edit", methods=["GET", "POST"])
@auth.login_required
def edit_post(post_id: int):
    post = db.query("SELECT * FROM forum_posts WHERE id = ?", (post_id,), one=True)
    if post is None or post["status"] == "deleted":
        abort(404)
    if post["status"] == "archived":
        abort(403, description="帖子已归档，内容已冻结")
    proposal = db.query("SELECT id FROM card_proposals WHERE forum_post_id = ?",
                        (post_id,), one=True)
    if proposal:
        abort(403, description="提案帖子由提案系统管理：请通过提案页修改后重新提交")
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
            return render_template("forum/form.html", categories=_postable_categories_for_user(),
                                   form_values=request.form, post=post, editing=True), 400
        db.execute(
            """UPDATE forum_posts SET title = ?, body = ?, category = ?,
               updated_at = datetime('now') WHERE id = ?""",
            (values["title"], values["body"], values["category"], post_id))
        auth.audit("forum_post_edit", "forum_post", post_id, values["title"])
        flash("帖子已更新", "success")
        return redirect(url_for("forum.detail", post_id=post_id))
    return render_template("forum/form.html", categories=_postable_categories_for_user(),
                           form_values=None, post=post, editing=True)


@bp.route("/forum/<int:post_id>/delete", methods=["POST"])
@auth.login_required
def delete_post(post_id: int):
    auth.check_csrf()
    post = db.query("SELECT author_id FROM forum_posts WHERE id = ?", (post_id,), one=True)
    if post is None:
        abort(404)
    proposal = db.query("SELECT id FROM card_proposals WHERE forum_post_id = ?",
                        (post_id,), one=True)
    if proposal:
        abort(403, description="提案帖子不能删除：审核结果需永久保留（管理员可归档）")
    user = auth.current_user()
    if post["author_id"] != user["id"] and not auth.is_moderator():
        auth.audit("forum_post_delete_denied", "forum_post", post_id)
        abort(403)
    db.execute("UPDATE forum_posts SET status = 'deleted', updated_at = datetime('now') "
               "WHERE id = ?", (post_id,))
    auth.audit("forum_post_delete", "forum_post", post_id)
    flash("帖子已删除", "success")
    return redirect(url_for("forum.list_posts"))


@bp.route("/forum/<int:post_id>/archive", methods=["POST"])
@auth.login_required
def archive_post(post_id: int):
    """版主归档 / 解除归档普通帖子。归档 = 内容冻结可查看，禁止回复与投票。"""
    auth.check_csrf()
    if not auth.is_moderator():
        abort(403)
    post = db.query("SELECT * FROM forum_posts WHERE id = ?", (post_id,), one=True)
    if post is None or post["status"] == "deleted":
        abort(404)
    action = request.form.get("action", "archive")
    if action not in ("archive", "unarchive"):
        abort(400)
    new_status = "archived" if action == "archive" else "visible"
    db.execute("UPDATE forum_posts SET status = ?, updated_at = datetime('now') "
               "WHERE id = ?", (new_status, post_id))
    auth.audit(f"forum_post_{action}", "forum_post", post_id, post["title"])
    flash("帖子已归档：内容可查看，但无法再回复与投票" if action == "archive"
          else "帖子已解除归档", "success")
    return redirect(url_for("forum.detail", post_id=post_id))


def _validate():
    """发帖/编辑共用校验。通过返回字段 dict，否则 None（flash 已设置）。"""
    title = (request.form.get("title") or "").strip()
    body = (request.form.get("body") or "").strip()
    category = (request.form.get("category") if request.form.get("category") in FORUM_CATEGORIES
                else DEFAULT_FORUM_CATEGORY)
    if category in PROPOSAL_CATEGORIES:
        flash("提案板块的帖子由提案系统自动创建，请从卡牌页面发起提案", "danger")
        return None
    if category in ADMIN_ONLY_CATEGORIES and not auth.is_admin():
        flash("该板块仅管理员可以发帖", "danger")
        return None
    if not (3 <= len(title) <= 80):
        flash("标题需要 3-80 个字符", "danger")
        return None
    if not body:
        flash("正文不能为空", "danger")
        return None
    return {"title": title, "body": body, "category": category}
