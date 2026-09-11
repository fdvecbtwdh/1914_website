"""管理后台 — 用户 / 卡牌 / Issue / 评论 / 举报 / 标签 / GitHub 同步 / 审计日志。
仅 admin。所有列表页支持筛选；主要列表支持勾选 + 批量操作。
"""
from flask import (Blueprint, abort, flash, make_response, redirect,
                   render_template, request, url_for)

from . import db, auth
from .gameconstants import (ISSUE_STATUSES, ISSUE_PRIORITIES, CARD_TYPES,
                            RARITIES, UNIT_CLASSES)

bp = Blueprint("admin", __name__, url_prefix="/admin")

PAGE_SIZE = 30

USER_ROLES = ("user", "moderator", "admin")
REPORT_TARGETS = ("card", "issue", "comment", "user")


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
        "open_reports": db.query("SELECT COUNT(*) AS n FROM reports WHERE status='open'",
                                 one=True)["n"],
        "pending_sync": db.query(
            "SELECT COUNT(*) AS n FROM sync_queue WHERE status='pending'", one=True)["n"],
    }
    recent_audit = db.query(
        """SELECT a.*, u.username AS actor_name FROM audit_log a
           LEFT JOIN users u ON u.id = a.actor_id ORDER BY a.id DESC LIMIT 12""")
    return render_template("admin/index.html", stats=stats, recent_audit=recent_audit)


# ---------- 用户管理 ----------

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
        f"""SELECT u.*, (SELECT COUNT(*) FROM cards c WHERE c.author_id = u.id) AS card_count,
           (SELECT COUNT(*) FROM issues i WHERE i.author_id = u.id) AS issue_count
           FROM users u WHERE {where_sql} ORDER BY u.id DESC LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, offset))
    return render_template("admin/users.html", users=rows, total=total, page=page,
                           pages=max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
                           q=q, role=role, status=status)


@bp.route("/users/batch", methods=["POST"])
def users_batch():
    auth.check_csrf()
    ids = _parse_ids()
    action = request.form.get("action", "")
    if not _batch_guard(action, ids, {"ban", "unban", "set_user", "set_moderator", "set_admin"}):
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
            db.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (uid,))
            auth.revoke_all_sessions(uid)
        elif action == "unban":
            db.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (uid,))
        else:
            role = action.split("_", 1)[1]
            db.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
            auth.revoke_all_sessions(uid)  # 角色变更后重新登录生效
        auth.audit(f"user_{action}", "user", uid, target["username"])
        done += 1
    flash(f"批量操作完成：成功 {done}，跳过 {skipped}", "success")
    return _back("admin.users")


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
        db.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (user_id,))
        auth.revoke_all_sessions(user_id)
        auth.audit("user_ban", "user", user_id, target["username"])
        flash(f"已封禁 {target['username']}", "success")
    elif action == "unban":
        db.execute("UPDATE users SET is_banned = 0 WHERE id = ?", (user_id,))
        auth.audit("user_unban", "user", user_id, target["username"])
        flash(f"已解封 {target['username']}", "success")
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

def _hide_reported_content(target_type: str, target_id: int) -> None:
    if target_type == "card":
        db.execute("UPDATE cards SET status='hidden' WHERE id = ?", (target_id,))
    elif target_type == "comment":
        db.execute("UPDATE comments SET is_deleted=1, body='', body_html='' WHERE id=?",
                   (target_id,))
    elif target_type == "user":
        db.execute("UPDATE users SET is_banned=1 WHERE id = ?", (target_id,))
        auth.revoke_all_sessions(target_id)
    elif target_type == "issue":
        db.execute("UPDATE issues SET status='closed', updated_at=datetime('now') "
                   "WHERE id = ?", (target_id,))


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
            _hide_reported_content(report["target_type"], report["target_id"])
            db.execute("UPDATE reports SET status='resolved', handled_by=? WHERE id=?",
                       (me["id"], rid))
        auth.audit(f"report_batch_{action}", report["target_type"], report["target_id"])
        done += 1
    label = {"resolve": "标记已处理", "dismiss": "忽略", "hide_content": "隐藏内容并处理"}[action]
    flash(f"批量操作完成：{label} {done} 条举报", "success")
    return _back("admin.reports")


def _report_target(target_type: str, target_id: int) -> dict | None:
    if target_type == "card":
        row = db.query("SELECT id, name FROM cards WHERE id = ?", (target_id,), one=True)
        return {"url": f"/card/{target_id}", "label": f"卡牌：{row['name']}"} if row else None
    if target_type == "issue":
        row = db.query("SELECT id, title FROM issues WHERE id = ?", (target_id,), one=True)
        return {"url": f"/issue/{target_id}", "label": f"Issue：{row['title']}"} if row else None
    if target_type == "comment":
        row = db.query("SELECT id, target_type, target_id, body FROM comments WHERE id = ?",
                       (target_id,), one=True)
        if row is None:
            return None
        url = f"/{'card' if row['target_type'] == 'card' else 'issue'}/{row['target_id']}"
        return {"url": url, "label": f"评论：{(row['body'] or '(已删除)')[:60]}"}
    if target_type == "user":
        row = db.query("SELECT id, username FROM users WHERE id = ?", (target_id,), one=True)
        return {"url": f"/u/{row['username']}", "label": f"用户：{row['username']}"} if row else None
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
