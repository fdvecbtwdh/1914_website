"""站内消息页 — 登录用户查看自己的消息（分页、按时间倒序）。
打开本页即视为"查看消息"：记录 last_viewed_messages_at，角标清零。
"""
from flask import Blueprint, redirect, render_template, request, url_for

from . import db, auth
from .notify import mark_viewed

bp = Blueprint("messages", __name__, url_prefix="/messages")

PAGE_SIZE = 20


PAGE_SIZE = 20

# 消息分类（页顶 tab）：被@ = 回复中提及你；被回复 = 直接回复你的评论；被点赞 = 收到的赞
CATEGORIES = {
    "mention": ("被@", ("comment_mention",)),
    "reply": ("被回复", ("comment_reply",)),
    "vote": ("被点赞", ("card_vote", "issue_vote")),
}


@bp.route("")
def inbox():
    user = auth.current_user()
    if user is None:
        return redirect(url_for("auth.login", next="/messages"))
    mark_viewed(user["id"])  # 打开任意分类都视为已查看（时间点机制）
    cat = request.args.get("cat", "")
    if cat not in CATEGORIES:
        cat = ""
    page = max(1, min(request.args.get("page", 1, type=int), 500))

    cond, args = "recipient_id = ?", [user["id"]]
    if cat:
        types = CATEGORIES[cat][1]
        cond += f" AND type IN ({','.join('?' * len(types))})"
        args += list(types)
    total = db.query(f"SELECT COUNT(*) AS n FROM notifications WHERE {cond}",
                     args, one=True)["n"]
    # 联表后 type 与 cards.type 重名，需要 n. 前缀
    rows = db.query(
        f"""SELECT n.id, n.type, n.card_id, n.issue_id, n.forum_post_id, n.comment_id,
                  n.detail, n.created_at, a.username AS actor_name, a.role AS actor_role,
                  c.name AS card_name, i.title AS issue_title, fp.title AS forum_title,
                  cm.is_deleted AS comment_deleted
           FROM notifications n
           LEFT JOIN users a ON a.id = n.actor_id
           LEFT JOIN cards c ON c.id = n.card_id
           LEFT JOIN issues i ON i.id = n.issue_id
           LEFT JOIN forum_posts fp ON fp.id = n.forum_post_id
           LEFT JOIN comments cm ON cm.id = n.comment_id
           WHERE n.{cond.replace(' AND type IN', ' AND n.type IN')}
           ORDER BY n.created_at DESC, n.id DESC
           LIMIT ? OFFSET ?""",
        args + [PAGE_SIZE, (page - 1) * PAGE_SIZE])
    items = []
    for r in rows:
        d = dict(r)
        d["text"] = _text(d)
        # 目标内容已被删除时不生成链接
        d["gone"] = (d["card_id"] and d["card_name"] is None) or \
                    (d["issue_id"] and d["issue_title"] is None) or \
                    (d["forum_post_id"] and d["forum_title"] is None)
        if d["gone"]:
            d["url"] = None
        elif d["card_id"]:
            d["url"] = f"/card/{d['card_id']}" + (f"#comment-{d['comment_id']}" if d["comment_id"] else "")
        elif d["forum_post_id"]:
            d["url"] = f"/forum/{d['forum_post_id']}" + (f"#comment-{d['comment_id']}" if d["comment_id"] else "")
        elif d["issue_id"]:
            d["url"] = f"/issue/{d['issue_id']}" + (f"#comment-{d['comment_id']}" if d["comment_id"] else "")
        else:
            d["url"] = None
        items.append(d)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    # 分类计数（tab 角标），一条 GROUP BY 搞定
    by_type: dict = {}
    for r in db.query(
            "SELECT type, COUNT(*) AS n FROM notifications WHERE recipient_id = ? GROUP BY type",
            (user["id"],)):
        by_type[r["type"]] = r["n"]
    counts = {"": sum(by_type.values())}
    for key, (_, types) in CATEGORIES.items():
        counts[key] = sum(by_type.get(t, 0) for t in types)
    return render_template("messages.html", items=items, total=total, page=page,
                           pages=pages, cat=cat,
                           qs=f"cat={cat}&" if cat else "", counts=counts)


def _text(d: dict) -> str:
    """还原消息文案；内容被删除时给出合理提示。"""
    actor = d["actor_name"] or "已注销用户"
    if d["type"] == "card_comment":
        return f"{actor} 评论了你的卡牌" + (f"《{d['card_name']}》" if d["card_name"] else "（该内容已被删除）")
    if d["type"] == "user_mute":
        return d["detail"] or "你已被禁言"
    if d["type"] == "user_unmute":
        return d["detail"] or "你的禁言已被管理员解除"
    if d["type"] == "user_ban":
        return d["detail"] or "你的账号已被封禁"
    if d["type"] == "forum_reply":
        return f"{actor} 回复了你的帖子" + (f"《{d['forum_title']}》" if d["forum_title"] else "（该内容已被删除）")
    if d["type"] == "comment_mention":
        ctx = d["card_name"] or d["issue_title"] or d["forum_title"]
        base = f"《{ctx}》" if ctx else ""
        return f"{actor} 在回复中提到了你" + (f"（{base}）" if base else "")
    if d["type"] == "comment_reply":
        ctx = d["card_name"] or d["issue_title"] or d["forum_title"]
        base = f"《{ctx}》" if ctx else ""
        return f"{actor} 回复了你的评论" + (f"（{base}）" if base else "")
    if d["type"] == "card_vote":
        return f"{actor} 点赞了你的卡牌" + (f"《{d['card_name']}》" if d["card_name"] else "")
    if d["type"] == "issue_comment":
        return f"{actor} 评论了你的 Issue" + (f"《{d['issue_title']}》" if d["issue_title"] else "（该内容已被删除）")
    if d["type"] == "issue_vote":
        return f"{actor} 点赞了你的 Issue" + (f"《{d['issue_title']}》" if d["issue_title"] else "（该内容已被删除）")
    if d["type"] == "proposal_returned":
        t = f"{actor} 打回了你的提案" + (f"《{d['forum_title']}》" if d["forum_title"] else "（该内容已被删除）")
        return t + (f"：{d['detail']}" if d["detail"] else "，请修改后重新提交")
    if d["type"] == "proposal_approved":
        return (f"{actor} 批准了你的提案" + (f"《{d['forum_title']}》" if d["forum_title"] else "")
                + (f"（{d['detail']}）" if d["detail"] else ""))
    if d["type"] == "proposal_rejected":
        return f"{actor} 未批准你的提案" + (f"《{d['forum_title']}》" if d["forum_title"] else "（该内容已被删除）")
    if d["type"] == "proposal_resubmitted":
        return (f"{actor} 重新提交了提案" + (f"《{d['forum_title']}》" if d["forum_title"] else "（该内容已被删除）")
                + "，等待审核")
    return f"{actor} 与你产生了互动"
