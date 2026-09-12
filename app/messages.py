"""站内消息页 — 登录用户查看自己的消息（分页、按时间倒序）。
打开本页即视为"查看消息"：记录 last_viewed_messages_at，角标清零。
"""
from flask import Blueprint, redirect, render_template, request, url_for

from . import db, auth
from .notify import mark_viewed

bp = Blueprint("messages", __name__, url_prefix="/messages")

PAGE_SIZE = 20


@bp.route("")
def inbox():
    user = auth.current_user()
    if user is None:
        return redirect(url_for("auth.login", next="/messages"))
    mark_viewed(user["id"])
    page = max(1, min(request.args.get("page", 1, type=int), 500))
    total = db.query("SELECT COUNT(*) AS n FROM notifications WHERE recipient_id = ?",
                     (user["id"],), one=True)["n"]
    rows = db.query(
        """SELECT n.id, n.type, n.card_id, n.issue_id, n.comment_id,
                  n.created_at, a.username AS actor_name,
                  c.name AS card_name, i.title AS issue_title,
                  cm.is_deleted AS comment_deleted
           FROM notifications n
           LEFT JOIN users a ON a.id = n.actor_id
           LEFT JOIN cards c ON c.id = n.card_id
           LEFT JOIN issues i ON i.id = n.issue_id
           LEFT JOIN comments cm ON cm.id = n.comment_id
           WHERE n.recipient_id = ?
           ORDER BY n.created_at DESC, n.id DESC
           LIMIT ? OFFSET ?""",
        (user["id"], PAGE_SIZE, (page - 1) * PAGE_SIZE))
    items = []
    for r in rows:
        d = dict(r)
        d["text"] = _text(d)
        # 目标内容已被删除时不生成链接
        d["gone"] = (d["card_id"] and d["card_name"] is None) or \
                    (d["issue_id"] and d["issue_title"] is None)
        if d["gone"]:
            d["url"] = None
        elif d["card_id"]:
            d["url"] = f"/card/{d['card_id']}" + (f"#comment-{d['comment_id']}" if d["comment_id"] else "")
        elif d["issue_id"]:
            d["url"] = f"/issue/{d['issue_id']}" + (f"#comment-{d['comment_id']}" if d["comment_id"] else "")
        else:
            d["url"] = None
        items.append(d)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    return render_template("messages.html", items=items, total=total, page=page, pages=pages)


def _text(d: dict) -> str:
    """还原消息文案；内容被删除时给出合理提示。"""
    actor = d["actor_name"] or "已注销用户"
    if d["type"] == "card_comment":
        return f"{actor} 评论了你的卡牌" + (f"《{d['card_name']}》" if d["card_name"] else "（该内容已被删除）")
    if d["type"] == "comment_reply":
        base = f"《{d['card_name']}》" if d["card_name"] else ""
        return f"{actor} 回复了你的评论" + (f"（{base}）" if base else "")
    if d["type"] == "card_vote":
        return f"{actor} 点赞了你的卡牌" + (f"《{d['card_name']}》" if d["card_name"] else "")
    if d["type"] == "issue_comment":
        return f"{actor} 评论了你的 Issue" + (f"《{d['issue_title']}》" if d["issue_title"] else "（该内容已被删除）")
    if d["type"] == "issue_vote":
        return f"{actor} 点赞了你的 Issue" + (f"《{d['issue_title']}》" if d["issue_title"] else "（该内容已被删除）")
    return f"{actor} 与你产生了互动"
