"""Bug / Issue 系统 — 类 GitHub Issues：列表 / 详情 / 创建 / 编辑 / 状态流转。"""
from flask import (Blueprint, abort, flash, redirect, render_template, request,
                   url_for)

from . import db, auth, interactions
from .gameconstants import (ISSUE_STATUSES, ISSUE_PRIORITIES, ISSUE_COMPONENTS,
                            DEFAULT_ISSUE_COMPONENT, ISSUE_TAGS)

bp = Blueprint("issues", __name__, url_prefix="")

PAGE_SIZE = 20

OPEN_LIKE_STATUSES = ("open", "in_progress")


def _issue_or_404(issue_id: int):
    row = db.query(
        """SELECT i.*, u.username AS author_name, u.role AS author_role FROM issues i
           LEFT JOIN users u ON u.id = i.author_id WHERE i.id = ?""", (issue_id,), one=True)
    if row is None:
        abort(404)
    return row


def _issue_labels(issue_id: int) -> list:
    return db.query(
        """SELECT l.id, l.name, l.color FROM content_labels cl
           JOIN labels l ON l.id = cl.label_id
           WHERE cl.content_type = 'issue' AND cl.content_id = ?""", (issue_id,))


# ---------- 列表 ----------

@bp.route("/issues")
def issue_list():
    q = (request.args.get("q") or "").strip()
    status = request.args.get("status", "")
    priority = request.args.get("priority", "")
    label = request.args.get("label", "")
    component = request.args.get("component", "")
    author = request.args.get("author", "").strip()
    sort = request.args.get("sort", "newest")
    page = max(1, min(request.args.get("page", 1, type=int), 500))

    where, args = ["1=1"], []
    if q:
        where.append("(i.title LIKE ? OR i.body LIKE ?)")
        like = f"%{q}%"
        args += [like, like]
    if status in ISSUE_STATUSES:
        where.append("i.status = ?")
        args.append(status)
    elif status != "all":
        where.append("i.status IN ('open','in_progress')")  # 默认只看未完成
    if priority in ISSUE_PRIORITIES:
        where.append("i.priority = ?")
        args.append(priority)
    if label:
        where.append("i.id IN (SELECT content_id FROM content_labels cl "
                     "JOIN labels l ON l.id = cl.label_id "
                     "WHERE cl.content_type = 'issue' AND l.name = ?)")
        args.append(label)
    if component in ISSUE_COMPONENTS:
        where.append("i.component = ?")
        args.append(component)
    if author:
        where.append("u.username = ?")
        args.append(author)

    order = {
        "oldest": "i.created_at ASC, i.id ASC",
        "updated": "i.updated_at DESC, i.id DESC",
        "hot": "vote_count DESC, i.created_at DESC",
    }.get(sort, "i.created_at DESC, i.id DESC")

    base_sql = f"""FROM issues i LEFT JOIN users u ON u.id = i.author_id
        LEFT JOIN (SELECT target_id, COUNT(*) vc FROM votes WHERE target_type='issue'
          GROUP BY target_id) v ON v.target_id = i.id
        LEFT JOIN (SELECT target_id, COUNT(*) cc FROM comments WHERE target_type='issue'
          AND is_deleted=0 GROUP BY target_id) cm ON cm.target_id = i.id
        WHERE {' AND '.join(where)}"""
    total = db.query(f"SELECT COUNT(*) AS n {base_sql}", tuple(args), one=True)["n"]
    rows = db.query(
        f"""SELECT i.*, u.username AS author_name, IFNULL(v.vc,0) AS vote_count,
            IFNULL(cm.cc,0) AS comment_count {base_sql}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE))
    issues = [dict(r) for r in rows]
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    counts = {
        "open_all": db.query(
            "SELECT COUNT(*) AS n FROM issues WHERE status IN ('open','in_progress')",
            one=True)["n"],
        "all": db.query("SELECT COUNT(*) AS n FROM issues", one=True)["n"],
    }
    for s in ISSUE_STATUSES:
        counts[s] = db.query("SELECT COUNT(*) AS n FROM issues WHERE status = ?", (s,),
                             one=True)["n"]
    return render_template("issues/list.html", issues=issues, total=total, page=page,
                           pages=pages, q=q, status=status, priority=priority, label=label,
                           component=component,
                           author=author, sort=sort, counts=counts,
                           statuses=ISSUE_STATUSES, priorities=ISSUE_PRIORITIES,
                           issue_tags=ISSUE_TAGS)


# ---------- 创建 ----------

@bp.route("/issues/new", methods=["GET", "POST"])
@auth.login_required
def issue_new():
    if request.method == "POST":
        auth.check_csrf()
        user = auth.current_user()
        mute = auth.active_mute(user)
        if mute:
            flash(auth.mute_notice(mute), "danger")
            return redirect(url_for("issues.issue_list"))
        if auth.throttle("sub_issue", user["id"], 3, 1):
            flash("提交过于频繁，请稍后再试", "danger")
            return redirect(url_for("issues.issue_new"))
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        if not (5 <= len(title) <= 120):
            flash("标题需 5–120 字符", "danger")
        elif len(body) > 20000:
            flash("内容过长", "danger")
        else:
            component = (request.form.get("component") or ""
                         if request.form.get("component") in ISSUE_COMPONENTS
                         else DEFAULT_ISSUE_COMPONENT)
            issue_id = db.execute(
                """INSERT INTO issues (title, body, game_version, sys_info, author_id, component)
                   VALUES (?,?,?,?,?,?)""",
                (title, body,
                 (request.form.get("game_version") or "").strip()[:40],
                 (request.form.get("sys_info") or "").strip()[:500],
                 auth.current_user()["id"], component))
            from .cards import _set_labels
            _set_labels("issue", issue_id, ",".join(
                t for t in request.form.getlist("issue_tags") if t in ISSUE_TAGS))
            auth.audit("issue_create", "issue", issue_id, title)
            # 加入 GitHub 同步队列（按所属分流仓库；未配置 Token 时 worker 会安全跳过）
            from .github_sync import enqueue
            enqueue(issue_id, "create")
            flash("Issue 已提交，感谢反馈！", "success")
            return redirect(url_for("issues.issue_detail", issue_id=issue_id))
    return render_template("issues/form.html", issue=None, editing=False,
                           game_version_default=__import__("flask").current_app.config["GAME_VERSION"],
                           issue_components=ISSUE_COMPONENTS,
                           default_component=DEFAULT_ISSUE_COMPONENT,
                           issue_tags=ISSUE_TAGS)


# ---------- 详情 ----------

@bp.route("/issue/<int:issue_id>")
def issue_detail(issue_id: int):
    issue = _issue_or_404(issue_id)
    comments = interactions.comments_for("issue", issue_id)
    my_vote = interactions.user_has_voted(auth.current_user()["id"], "issue", issue_id) \
        if auth.is_logged_in() else False
    dup_of = None
    if issue["duplicate_of"]:
        dup_of = db.query("SELECT id, title, status FROM issues WHERE id = ?",
                          (issue["duplicate_of"],), one=True)
    dups = db.query(
        "SELECT id, title FROM issues WHERE duplicate_of = ? AND status != 'closed' LIMIT 5",
        (issue_id,))
    return render_template("issues/detail.html", issue=issue, comments=comments,
                           my_vote=my_vote, labels=_issue_labels(issue_id),
                           statuses=ISSUE_STATUSES, priorities=ISSUE_PRIORITIES,
                           dup_of=dup_of, dups=dups,
                           can_moderate=auth.is_moderator())


# ---------- 编辑 ----------

@bp.route("/issue/<int:issue_id>/edit", methods=["GET", "POST"])
@auth.login_required
def issue_edit(issue_id: int):
    issue = _issue_or_404(issue_id)
    user = auth.current_user()
    is_mod = auth.is_moderator()
    if issue["author_id"] != user["id"] and not is_mod:
        abort(403)
    if request.method == "POST":
        auth.check_csrf()
        mute = auth.active_mute(user)
        if mute:
            flash(auth.mute_notice(mute), "danger")
            return redirect(url_for("issues.issue_detail", issue_id=issue_id))
        title = (request.form.get("title") or "").strip()
        body = (request.form.get("body") or "").strip()
        if not (5 <= len(title) <= 120):
            flash("标题需 5–120 字符", "danger")
        elif len(body) > 20000:
            flash("内容过长", "danger")
        else:
            component = request.form.get("component")
            component = component if component in ISSUE_COMPONENTS else issue["component"]
            db.execute(
                """UPDATE issues SET title = ?, body = ?, component = ?,
                   updated_at = datetime('now') WHERE id = ?""",
                (title, body, component, issue_id))
            from .cards import _set_labels
            _set_labels("issue", issue_id, ",".join(
                t for t in request.form.getlist("issue_tags") if t in ISSUE_TAGS))
            from .github_sync import enqueue
            enqueue(issue_id, "update")
            auth.audit("issue_edit", "issue", issue_id, title)
            flash("Issue 已更新", "success")
            return redirect(url_for("issues.issue_detail", issue_id=issue_id))
    return render_template("issues/form.html", issue=issue, editing=True,
                           game_version_default=issue["game_version"],
                           issue_components=ISSUE_COMPONENTS,
                           default_component=issue["component"],
                           issue_tags=ISSUE_TAGS,
                           current_tags=[l["name"] for l in _issue_labels(issue_id)])


# ---------- 状态 / 优先级 / 标签（版主以上） ----------

@bp.route("/issue/<int:issue_id>/moderate", methods=["POST"])
@auth.login_required
def issue_moderate(issue_id: int):
    auth.check_csrf()
    if not auth.is_moderator():
        abort(403)
    issue = _issue_or_404(issue_id)
    action = request.form.get("action", "")
    status = request.form.get("status", "")
    priority = request.form.get("priority", "")
    dup_raw = request.form.get("duplicate_of", "")

    if action == "status" and status in ISSUE_STATUSES:
        dup_of = None
        if status == "duplicate":
            try:
                dup_of = int(dup_raw)
            except ValueError:
                flash("请填写重复 Issue 的编号", "warning")
                return redirect(url_for("issues.issue_detail", issue_id=issue_id))
            if dup_of == issue_id or db.query(
                    "SELECT 1 FROM issues WHERE id = ?", (dup_of,), one=True) is None:
                flash("重复 Issue 编号无效", "warning")
                return redirect(url_for("issues.issue_detail", issue_id=issue_id))
        db.execute("UPDATE issues SET status = ?, duplicate_of = ?, updated_at = datetime('now') "
                   "WHERE id = ?", (status, dup_of, issue_id))
        auth.audit("issue_status", "issue", issue_id, status)
        from .github_sync import enqueue
        enqueue(issue_id, "reopen" if status in OPEN_LIKE_STATUSES else "close")
        flash(f"状态已更新为 {ISSUE_STATUSES[status]}", "success")
    elif action == "priority" and priority in ISSUE_PRIORITIES:
        db.execute("UPDATE issues SET priority = ?, updated_at = datetime('now') WHERE id = ?",
                   (priority, issue_id))
        auth.audit("issue_priority", "issue", issue_id, priority)
        flash("优先级已更新", "success")
    elif action == "labels":
        from .cards import _set_labels
        _set_labels("issue", issue_id, request.form.get("tags", ""))
        auth.audit("issue_labels", "issue", issue_id)
        flash("标签已更新", "success")
    return redirect(url_for("issues.issue_detail", issue_id=issue_id))
