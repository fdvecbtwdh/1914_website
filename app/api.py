"""JSON API — 投票切换、评论增删改、举报、实时预览。
所有写操作：登录 + CSRF + 服务端校验。
"""
from flask import Blueprint, jsonify, request, abort, session
import secrets as _secrets

from . import db, auth, interactions, uploads
from .markdown_utils import render_markdown

bp = Blueprint("api", __name__, url_prefix="/api")


def _json_error(code: int, message: str):
    return jsonify({"ok": False, "error": message}), code


def _require_login_json():
    u = auth.current_user()
    if u is None:
        abort(401, description="请先登录")
    if u["is_banned"]:
        abort(403, description="账号已被封禁" +
              (f"（原因：{u['banned_reason']}）" if u["banned_reason"] else ""))
    return u


@bp.before_request
def _csrf_protect():
    """API 写请求必须带 CSRF token（header 或 form）。
    未登录访客先让视图返回 401；已建立会话的请求强制校验 CSRF。"""
    if request.method in ("POST", "PUT", "DELETE"):
        good = session.get("csrf", "")
        if not good:
            return  # 无会话 → 由视图做登录检查（401）
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not token or not _secrets.compare_digest(token, good):
            abort(400, description="CSRF 校验失败")


# ---------- 投票 ----------

@bp.route("/vote/<target_type>/<int:target_id>", methods=["POST"])
def vote(target_type: str, target_id: int):
    user = _require_login_json()
    # 提案投票带方向（1=赞成 / -1=反对）；其余类型忽略 direction
    direction = -1 if request.form.get("direction") == "-1" else 1
    try:
        result = interactions.vote(user["id"], target_type, target_id, direction)
    except LookupError:
        return _json_error(404, "目标不存在")
    except ValueError as e:
        return _json_error(400, str(e))
    return jsonify({"ok": True, **result})


# ---------- 评论 ----------

@bp.route("/comment/<target_type>/<int:target_id>", methods=["POST"])
def comment_new(target_type: str, target_id: int):
    user = _require_login_json()
    mute = auth.active_mute(user)
    if mute:
        return _json_error(403, auth.mute_notice(mute))
    if auth.throttle("sub_comment", user["id"], 10, 1):
        return _json_error(429, "评论过于频繁，请稍后再试")
    body = (request.form.get("body") or "").strip()
    parent_raw = request.form.get("parent_id", "")
    parent_id = int(parent_raw) if parent_raw.isdigit() else None
    try:
        cid = interactions.add_comment(user["id"], target_type, target_id, body, parent_id)
    except LookupError as e:
        return _json_error(404, str(e))
    except ValueError as e:
        return _json_error(400, str(e))
    row = db.query("SELECT * FROM comments WHERE id = ?", (cid,), one=True)
    return jsonify({"ok": True, "id": cid, "html": row["body_html"]})


@bp.route("/comment/<int:comment_id>/edit", methods=["POST"])
def comment_edit(comment_id: int):
    user = _require_login_json()
    mute = auth.active_mute(user)
    if mute:
        return _json_error(403, auth.mute_notice(mute))
    body = (request.form.get("body") or "").strip()
    if not body:
        return _json_error(400, "内容不能为空")
    try:
        interactions.edit_comment(user["id"], comment_id, body, auth.is_moderator())
    except PermissionError:
        return _json_error(403, "只能编辑自己的评论")
    except LookupError:
        return _json_error(404, "评论不存在")
    row = db.query("SELECT body_html FROM comments WHERE id = ?", (comment_id,), one=True)
    return jsonify({"ok": True, "html": row["body_html"]})


@bp.route("/comment/<int:comment_id>/delete", methods=["POST"])
def comment_delete(comment_id: int):
    user = _require_login_json()
    try:
        interactions.delete_comment(user["id"], comment_id, auth.is_moderator())
    except PermissionError:
        return _json_error(403, "只能删除自己的评论")
    except LookupError:
        return _json_error(404, "评论不存在")
    return jsonify({"ok": True})


# ---------- 举报 ----------

@bp.route("/report", methods=["POST"])
def report():
    user = _require_login_json()
    try:
        interactions.add_report(
            user["id"],
            request.form.get("target_type", ""),
            int(request.form.get("target_id", 0) or 0),
            request.form.get("reason", ""))
    except ValueError as e:
        return _json_error(400, str(e))
    except LookupError:
        return _json_error(404, "目标不存在")
    return jsonify({"ok": True, "message": "举报已提交，感谢反馈"})


# ---------- Markdown 实时预览（投稿/Issue 编辑用） ----------

@bp.route("/preview", methods=["POST"])
def preview():
    body = request.form.get("body", "")[:20000]
    return jsonify({"ok": True, "html": render_markdown(body)})
