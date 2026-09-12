"""通用互动逻辑 — 投票、评论、举报。卡牌与 Issue 复用。
投票唯一性由 votes 表主键 (user_id, target_type, target_id) 保证，可重入。
"""
from . import db, auth

TARGET_TYPES = ("card", "issue", "comment")
VALID_TARGETS = {
    "card": ("cards", "card"),
    "issue": ("issues", "issue"),
    "comment": ("comments", "comment"),
}


def _target_exists(target_type: str, target_id: int) -> bool:
    if target_type not in VALID_TARGETS:
        return False
    table, _ = VALID_TARGETS[target_type]
    if target_type == "comment":
        row = db.query(f"SELECT 1 FROM {table} WHERE id = ? AND is_deleted = 0",
                       (target_id,), one=True)
    else:
        row = db.query(f"SELECT 1 FROM {table} WHERE id = ?", (target_id,), one=True)
    return row is not None


# ---------- 投票 ----------

def vote(user_id: int, target_type: str, target_id: int) -> dict:
    """切换投票：已投则取消，未投则投上。返回 {voted: bool, count: int}。"""
    if target_type not in VALID_TARGETS:
        raise ValueError("无效的投票对象")
    if not _target_exists(target_type, target_id):
        raise LookupError("目标不存在")
    existing = db.query(
        "SELECT 1 FROM votes WHERE user_id = ? AND target_type = ? AND target_id = ?",
        (user_id, target_type, target_id), one=True)
    if existing:
        db.execute("DELETE FROM votes WHERE user_id = ? AND target_type = ? AND target_id = ?",
                   (user_id, target_type, target_id))
        voted = False
    else:
        try:
            db.execute(
                "INSERT INTO votes (user_id, target_type, target_id) VALUES (?,?,?)",
                (user_id, target_type, target_id))
            voted = True
            _notify_vote(user_id, target_type, target_id)
        except Exception:
            # 并发下唯一约束兜底：说明已投过，视为取消意图重试一次
            db.execute("DELETE FROM votes WHERE user_id = ? AND target_type = ? AND target_id = ?",
                       (user_id, target_type, target_id))
            voted = False
    return {"voted": voted, "count": vote_count(target_type, target_id)}


def _notify_vote(voter_id: int, target_type: str, target_id: int) -> None:
    """投票成功后通知内容作者（自己的内容不通知）。"""
    from .notify import notify
    if target_type == "card":
        author = db.query("SELECT author_id FROM cards WHERE id = ?", (target_id,), one=True)
        if author and author["author_id"] and author["author_id"] != voter_id:
            notify(author["author_id"], voter_id, "card_vote", card_id=target_id)
    elif target_type == "issue":
        author = db.query("SELECT author_id FROM issues WHERE id = ?", (target_id,), one=True)
        if author and author["author_id"] and author["author_id"] != voter_id:
            notify(author["author_id"], voter_id, "issue_vote", issue_id=target_id)


def vote_count(target_type: str, target_id: int) -> int:
    row = db.query("SELECT COUNT(*) AS c FROM votes WHERE target_type = ? AND target_id = ?",
                   (target_type, target_id), one=True)
    return row["c"] if row else 0


def user_has_voted(user_id: int, target_type: str, target_id: int) -> bool:
    if not user_id:
        return False
    return db.query(
        "SELECT 1 FROM votes WHERE user_id = ? AND target_type = ? AND target_id = ?",
        (user_id, target_type, target_id), one=True) is not None


def user_votes_map(user_id: int, target_type: str, ids: list[int]) -> set[int]:
    """列表页批量查询当前用户已投票的目标 id 集合。"""
    if not user_id or not ids:
        return set()
    placeholders = ",".join("?" * len(ids))
    rows = db.query(
        f"SELECT target_id FROM votes WHERE user_id = ? AND target_type = ? "
        f"AND target_id IN ({placeholders})",
        (user_id, target_type, *ids))
    return {r["target_id"] for r in rows}


def vote_counts_map(target_type: str, ids: list[int]) -> dict[int, int]:
    """列表页批量取投票数。"""
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = db.query(
        f"SELECT target_id, COUNT(*) AS c FROM votes WHERE target_type = ? "
        f"AND target_id IN ({placeholders}) GROUP BY target_id",
        (target_type, *ids))
    return {r["target_id"]: r["c"] for r in rows}


# ---------- 评论 ----------

def add_comment(user_id: int, target_type: str, target_id: int,
                body: str, parent_id: int | None = None) -> int:
    from .markdown_utils import render_markdown
    if target_type not in ("card", "issue"):
        raise ValueError("无效的评论对象")
    if not body.strip():
        raise ValueError("评论内容不能为空")
    if not _target_exists(target_type, target_id):
        raise LookupError("目标不存在")
    reply_to = None  # 被回复的评论；若它本身是子回复，正文加 @作者 前缀
    mention = False  # 回复的是子回复 → 通知归入"被@"（comment_mention）
    if parent_id is not None:
        reply_to = db.query(
            """SELECT c.id, c.target_type, c.target_id, c.parent_id, c.author_id, u.username
               FROM comments c LEFT JOIN users u ON u.id = c.author_id
               WHERE c.id = ? AND c.is_deleted = 0""",
            (parent_id,), one=True)
        if reply_to is None or reply_to["target_type"] != target_type \
                or reply_to["target_id"] != target_id:
            raise LookupError("回复的评论不存在")
        if reply_to["parent_id"] is not None:
            # 两层 DOM：仍挂到顶级评论，用 @用户名 前缀标明在回复谁
            mention = True
            parent_id = reply_to["parent_id"]
            if reply_to["username"]:
                body = f"@{reply_to['username']} " + body.strip()
    body = body.strip()
    cid = db.execute(
        "INSERT INTO comments (target_type, target_id, author_id, parent_id, body, body_html) "
        "VALUES (?,?,?,?,?,?)",
        (target_type, target_id, user_id, parent_id, body, render_markdown(body)))

    # ---- 站内消息 ----
    from .notify import notify
    if target_type == "card":
        card = db.query("SELECT author_id, name FROM cards WHERE id = ?",
                        (target_id,), one=True)
        if card and card["author_id"] and card["author_id"] != user_id and not parent_id:
            notify(card["author_id"], user_id, "card_comment", card_id=target_id,
                   comment_id=cid)
    else:
        issue = db.query("SELECT author_id FROM issues WHERE id = ?",
                         (target_id,), one=True)
        if issue and issue["author_id"] and issue["author_id"] != user_id and not parent_id:
            notify(issue["author_id"], user_id, "issue_comment", issue_id=target_id,
                   comment_id=cid)
    # 通知被回复评论的作者（回复子回复时是子回复作者，而非顶级评论作者）
    if reply_to and reply_to["author_id"] and reply_to["author_id"] != user_id:
        notify(reply_to["author_id"], user_id,
               "comment_mention" if mention else "comment_reply",
               card_id=target_id if target_type == "card" else None,
               issue_id=target_id if target_type == "issue" else None,
               comment_id=cid)
    return cid


def edit_comment(user_id: int, comment_id: int, body: str, is_mod: bool) -> None:
    from .markdown_utils import render_markdown
    row = db.query("SELECT * FROM comments WHERE id = ? AND is_deleted = 0", (comment_id,), one=True)
    if row is None:
        raise LookupError("评论不存在")
    if row["author_id"] != user_id and not is_mod:
        auth.audit("comment_edit_denied", "comment", comment_id)
        raise PermissionError("只能编辑自己的评论")
    db.execute("UPDATE comments SET body = ?, body_html = ?, edited_at = datetime('now') WHERE id = ?",
               (body.strip(), render_markdown(body.strip()), comment_id))


def delete_comment(user_id: int, comment_id: int, is_mod: bool) -> None:
    row = db.query("SELECT * FROM comments WHERE id = ? AND is_deleted = 0", (comment_id,), one=True)
    if row is None:
        raise LookupError("评论不存在")
    if row["author_id"] != user_id and not is_mod:
        auth.audit("comment_delete_denied", "comment", comment_id)
        raise PermissionError("只能删除自己的评论")
    db.execute("UPDATE comments SET is_deleted = 1, body = '', body_html = '' WHERE id = ?",
               (comment_id,))
    auth.audit("comment_delete", "comment", comment_id)


def comments_for(target_type: str, target_id: int) -> list[dict]:
    """返回两层结构的评论树 [{comment, replies: [...]}]。"""
    rows = db.query(
        """SELECT c.*, u.username, u.role AS author_role
           FROM comments c LEFT JOIN users u ON u.id = c.author_id
           WHERE c.target_type = ? AND c.target_id = ?
           ORDER BY c.created_at ASC, c.id ASC""",
        (target_type, target_id))
    tops, replies = [], {}
    for r in rows:
        item = dict(r)
        item["replies"] = []
        if r["parent_id"] is None:
            tops.append(item)
        else:
            replies.setdefault(r["parent_id"], []).append(item)
    for t in tops:
        t["replies"] = replies.get(t["id"], [])
    return tops


def comment_count(target_type: str, target_id: int) -> int:
    row = db.query(
        "SELECT COUNT(*) AS c FROM comments WHERE target_type = ? AND target_id = ? AND is_deleted = 0",
        (target_type, target_id), one=True)
    return row["c"] if row else 0


def comment_counts_map(target_type: str, ids: list[int]) -> dict[int, int]:
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = db.query(
        f"SELECT target_id, COUNT(*) AS c FROM comments WHERE target_type = ? "
        f"AND is_deleted = 0 AND target_id IN ({placeholders}) GROUP BY target_id",
        (target_type, *ids))
    return {r["target_id"]: r["c"] for r in rows}


# ---------- 举报 ----------

REPORTABLE = ("card", "issue", "comment", "user")


def add_report(reporter_id: int, target_type: str, target_id: int, reason: str) -> int:
    if target_type not in REPORTABLE:
        raise ValueError("无效的举报对象")
    if not reason.strip():
        raise ValueError("请填写举报理由")
    if target_type == "user":
        ok = db.query("SELECT 1 FROM users WHERE id = ?", (target_id,), one=True) is not None
    else:
        ok = _target_exists(target_type, target_id)
    if not ok:
        raise LookupError("目标不存在")
    # 同一用户对同一目标只保留一条未处理举报
    dup = db.query(
        "SELECT 1 FROM reports WHERE reporter_id = ? AND target_type = ? AND target_id = ? "
        "AND status = 'open'", (reporter_id, target_type, target_id), one=True)
    if dup:
        raise ValueError("你已举报过该内容，请等待管理员处理")
    return db.execute(
        "INSERT INTO reports (reporter_id, target_type, target_id, reason) VALUES (?,?,?,?)",
        (reporter_id, target_type, target_id, reason.strip()[:500]))
