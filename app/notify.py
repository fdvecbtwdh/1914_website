"""站内消息核心 — 通知创建（去重）、未读计数、查看位置标记。
消息类型（type）设计为可扩展：
  card_comment  有人评论了你的卡牌
  comment_reply 有人回复了你的评论（直接回复）
  comment_mention 回复中 @ 提到了你（回复子回复）
  card_vote     有人点赞了你的卡牌
  issue_comment 有人评论了你的 Issue
  issue_vote    有人点赞了你的 Issue
以后新增互动只需调用 notify() 并定义新的 type 与展示文案。
"""
from . import db


def notify(recipient_id, actor_id, ntype, card_id=None, issue_id=None,
           forum_post_id=None, comment_id=None) -> None:
    """创建一条站内消息。
    - 自己操作自己的内容不通知；
    - dedup_key 唯一约束防重复（前端重复提交/网络重试不会产生重复消息）。
    """
    if not recipient_id or not actor_id or recipient_id == actor_id:
        return
    anchor = comment_id or card_id or issue_id or forum_post_id
    dedup = f"{ntype}:{actor_id}:{anchor}"
    try:
        db.execute(
            """INSERT OR IGNORE INTO notifications
               (recipient_id, actor_id, type, card_id, issue_id, forum_post_id, comment_id, dedup_key)
               VALUES (?,?,?,?,?,?,?,?)""",
            (recipient_id, actor_id, ntype, card_id, issue_id, forum_post_id, comment_id, dedup))
    except Exception:
        pass  # 唯一约束冲突 = 重复消息，静默忽略


def unread_count(user_id: int) -> int:
    """自上次查看消息之后新产生的消息数。"""
    row = db.query(
        """SELECT COUNT(*) AS n FROM notifications
           WHERE recipient_id = ?
             AND created_at > COALESCE((SELECT last_viewed_messages_at FROM users WHERE id = ?),
                                       '1970-01-01')""",
        (user_id, user_id), one=True)
    return row["n"] if row else 0


def mark_viewed(user_id: int) -> None:
    db.execute("UPDATE users SET last_viewed_messages_at = datetime('now') WHERE id = ?",
               (user_id,))
