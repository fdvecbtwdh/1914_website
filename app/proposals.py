"""卡牌提案 — 修改提案（官方卡）与转正提案（玩家自制卡）共用一套流程。

- 数据存 card_proposals（type: modification/promotion 区分），每次提交/重提
  存一份版本快照（card_proposal_versions）。
- 提交后自动在论坛对应板块发帖；评论/回复/举报/通知完全复用论坛帖子体系。
- 投票挂在提案上（±1，可切换/取消）→ 打回重提沿用原帖子与原分数。
- 管理员批准时由服务端把提案字段直接写入正式卡牌（修改）或转正为官方
  （转正：source 置 official，不复制卡牌），并冻结 before_data 快照、归档帖子。
"""
import json

from flask import (Blueprint, abort, current_app, flash, redirect,
                   render_template, request, url_for)

from . import db, auth, interactions
from .gameconstants import (CARD_TYPES, UNIT_CLASSES, RARITIES, NATIONS, RANGES,
                            ABILITIES, PROPOSAL_STATUSES, PROPOSAL_TYPES)

bp = Blueprint("proposals", __name__)

# 提案可修改的字段（= 卡牌编辑器字段子集；不含 source/tag/art/标签）
EDITABLE_FIELDS = ("name", "type", "unit_class", "nation", "rarity",
                   "cost_g", "cost_z", "cost_oil", "attack", "defense",
                   "vision_range", "attack_range", "abilities",
                   "flavor_text", "description")

FIELD_LABELS = {
    "name": "名称", "type": "类型", "unit_class": "兵种", "nation": "阵营",
    "rarity": "稀有度", "cost_g": "经济 G", "cost_z": "Z / K 消耗",
    "cost_oil": "油费", "attack": "攻击", "defense": "防御",
    "vision_range": "视野范围", "attack_range": "攻击范围",
    "abilities": "词条", "flavor_text": "风味文字", "description": "描述",
}


def _parse_abilities(raw) -> list[str]:
    if isinstance(raw, list):
        return raw
    try:
        arr = json.loads(raw or "[]")
        return arr if isinstance(arr, list) else []
    except json.JSONDecodeError:
        return []


def _fmt_field(key: str, value) -> str:
    """差异表里的人类可读值。"""
    if key == "abilities":
        return "、".join(_parse_abilities(value)) or "—"
    if key == "type":
        return CARD_TYPES.get(value, value or "—")
    if key == "unit_class":
        return UNIT_CLASSES.get(value, value or "—")
    if key == "nation":
        return NATIONS.get(value, value or "—")
    if key == "rarity":
        return f"{RARITIES.get(value, value or '—')}卡"
    if key in ("vision_range", "attack_range"):
        return RANGES.get(value, value or "—")
    text = str(value) if value not in (None, "") else "—"
    if key == "description" and len(text) > 80:
        text = text[:80] + "…"
    return text


def diff_rows(original: dict, proposed: dict) -> list[dict]:
    """原卡 vs 提案卡的逐字段差异（模板渲染用）。"""
    rows = []
    for key in EDITABLE_FIELDS:
        old_raw, new_raw = original.get(key), proposed.get(key)
        if key == "abilities":
            changed = _parse_abilities(old_raw) != _parse_abilities(new_raw)
        else:
            changed = (old_raw or "") != (new_raw or "")
        rows.append({"key": key, "label": FIELD_LABELS[key], "changed": changed,
                     "old": _fmt_field(key, old_raw), "new": _fmt_field(key, new_raw)})
    return rows


def _proposal_or_404(proposal_id: int) -> dict:
    row = db.query(
        """SELECT p.*, u.username AS author_name FROM card_proposals p
           LEFT JOIN users u ON u.id = p.author_id WHERE p.id = ?""",
        (proposal_id,), one=True)
    if row is None:
        abort(404)
    return dict(row)


def _card_row(card_id: int) -> dict:
    row = db.query("SELECT * FROM cards WHERE id = ?", (card_id,), one=True)
    if row is None:
        abort(404)
    return dict(row)


def proposal_view(p: dict) -> dict:
    """提案行 → 模板友好 dict（含状态/类型文案、分数、关联卡牌与差异）。"""
    from . import cards as cards_mod
    p = dict(p)
    p["status_label"] = PROPOSAL_STATUSES.get(p["status"], p["status"])
    p["type_label"] = PROPOSAL_TYPES.get(p["type"], p["type"])
    p["score"] = interactions.vote_score("proposal", p["id"])
    p["data_dict"] = json.loads(p["data"] or "{}")

    card_row = db.query("SELECT c.*, u.username AS author_name FROM cards c "
                        "LEFT JOIN users u ON u.id = c.author_id WHERE c.id = ?",
                        (p["card_id"],), one=True)
    original = dict(card_row) if card_row else {}
    # 归档（已批准）提案以冻结的原版快照做对比，保证"批准时"的视图
    if p["before_data"]:
        try:
            original.update(json.loads(p["before_data"]))
        except json.JSONDecodeError:
            pass
    p["original_card"] = cards_mod.decorate_card(original) if original else None
    p["proposal_card"] = cards_mod.decorate_card({**original, **p["data_dict"]})
    p["diff"] = diff_rows(original, p["data_dict"])
    if p["reviewed_by"]:
        rev = db.query("SELECT username FROM users WHERE id = ?", (p["reviewed_by"],), one=True)
        p["reviewer_name"] = rev["username"] if rev else None
    else:
        p["reviewer_name"] = None
    p["versions"] = db.query(
        """SELECT v.*, u.username AS submitter_name FROM card_proposal_versions v
           LEFT JOIN users u ON u.id = v.submitted_by
           WHERE v.proposal_id = ? ORDER BY v.created_at DESC, v.id DESC""",
        (p["id"],))
    return p


def _validate_proposal_form() -> tuple[dict | None, str | None]:
    """复用卡牌编辑器校验；提案另需说明（reason）。"""
    from . import cards as cards_mod
    fields, err = cards_mod._validate_card_form(request.form)
    if err:
        return None, err
    reason = (request.form.get("reason") or "").strip()
    if len(reason) < 10:
        return None, "提案说明至少 10 个字：请写清修改/转正的理由与思路"
    if len(reason) > 5000:
        return None, "提案说明过长（最多 5000 字符）"
    fields.pop("card_tag", None)  # 提案不携带性质：修改沿用原卡，转正在批准时按版本判定
    return {"fields": fields, "reason": reason}, None


# ---------- 发起提案（官方卡=修改提案 / 玩家自制卡=转正提案） ----------

@bp.route("/card/<int:card_id>/propose", methods=["GET", "POST"])
@auth.login_required
def propose(card_id: int):
    card = _card_row(card_id)
    if card["status"] != "visible":
        abort(404)
    ptype = "modification" if card["source"] == "official" else "promotion"
    user = auth.current_user()
    if ptype == "promotion" and card["author_id"] != user["id"] and user["role"] != "admin":
        abort(403, description="只能为自己的玩家自制卡牌发起转正提案")

    if request.method == "POST":
        auth.check_csrf()
        mute = auth.active_mute(user)
        if mute:
            flash(auth.mute_notice(mute), "danger")
            return redirect(f"/card/{card_id}")
        if auth.throttle("sub_proposal", user["id"], 3, 1):
            flash("提案提交过于频繁，请稍后再试", "danger")
            return render_template("proposals/form.html", **_form_context(card, ptype, None, request.form)), 429
        values, err = _validate_proposal_form()
        if values is None:
            flash(err, "danger")
            return render_template("proposals/form.html", **_form_context(card, ptype, None, request.form)), 400

        category = "卡牌修改提案" if ptype == "modification" else "卡牌转正"
        title = f"[{'修改提案' if ptype == 'modification' else '转正提案'}] 《{card['name']}》"
        data_json = json.dumps(values["fields"], ensure_ascii=False)
        proposal_id = db.execute(
            """INSERT INTO card_proposals (type, card_id, author_id, data, reason)
               VALUES (?,?,?,?,?)""",
            (ptype, card_id, user["id"], data_json, values["reason"]))
        db.execute(
            """INSERT INTO card_proposal_versions (proposal_id, data, reason, submitted_by)
               VALUES (?,?,?,?)""",
            (proposal_id, data_json, values["reason"], user["id"]))
        post_id = db.execute(
            "INSERT INTO forum_posts (title, body, category, author_id) VALUES (?,?,?,?)",
            (title, values["reason"], category, user["id"]))
        db.execute("UPDATE card_proposals SET forum_post_id = ? WHERE id = ?",
                   (post_id, proposal_id))
        auth.audit("proposal_create", "card_proposal", proposal_id,
                   f"{title}（{category}）")
        flash("提案已提交，论坛讨论帖已同步创建", "success")
        return redirect(url_for("forum.detail", post_id=post_id))
    return render_template("proposals/form.html", **_form_context(card, ptype, None, None))


def _form_context(card: dict, ptype: str, proposal: dict | None, form_values) -> dict:
    """提案表单（创建/重提共用）的模板上下文。"""
    from . import cards as cards_mod
    base = cards_mod.decorate_card(card)
    # 表单预填：重提用提案数据，创建用原卡当前数据
    source = json.loads(proposal["data"]) if proposal else \
        {f: card.get(f) for f in EDITABLE_FIELDS}
    prefill = {**card, **source}
    prefill["abilities_list"] = _parse_abilities(prefill.get("abilities"))
    return {
        "base_card": base,
        "card": prefill,
        "ptype": ptype,
        "type_label": PROPOSAL_TYPES[ptype],
        "category": "卡牌修改提案" if ptype == "modification" else "卡牌转正",
        "proposal": proposal,
        "form_values": form_values,
        "resubmit": proposal is not None,
        "card_types": CARD_TYPES, "unit_classes": UNIT_CLASSES,
        "rarities": RARITIES, "nations": NATIONS, "abilities": ABILITIES,
    }


# ---------- 打回后：作者重新编辑并提交（沿用原帖，保留分数） ----------

def _own_returned_proposal(proposal_id: int) -> dict:
    p = _proposal_or_404(proposal_id)
    user = auth.current_user()
    if p["author_id"] != user["id"]:
        abort(403, description="只能操作自己的提案")
    if p["status"] == "returned":
        return p
    if p["status"] in ("approved", "rejected"):
        abort(403, description="提案已归档，不能再修改")
    abort(403, description="提案当前不在可修改状态（等待管理员打回后才能修改）")


@bp.route("/proposals/<int:proposal_id>/edit", methods=["GET"])
@auth.login_required
def edit(proposal_id: int):
    p = _own_returned_proposal(proposal_id)
    card = _card_row(p["card_id"])
    return render_template("proposals/form.html",
                           **_form_context(card, p["type"], p, None))


@bp.route("/proposals/<int:proposal_id>/resubmit", methods=["POST"])
@auth.login_required
def resubmit(proposal_id: int):
    auth.check_csrf()
    p = _own_returned_proposal(proposal_id)
    user = auth.current_user()
    mute = auth.active_mute(user)
    if mute:
        flash(auth.mute_notice(mute), "danger")
        return redirect(url_for("forum.detail", post_id=p["forum_post_id"]))
    values, err = _validate_proposal_form()
    if values is None:
        flash(err, "danger")
        return redirect(url_for("proposals.edit", proposal_id=proposal_id))
    data_json = json.dumps(values["fields"], ensure_ascii=False)
    db.execute(
        """INSERT INTO card_proposal_versions (proposal_id, data, reason, submitted_by)
           VALUES (?,?,?,?)""",
        (proposal_id, data_json, values["reason"], user["id"]))
    db.execute(
        """UPDATE card_proposals SET data = ?, reason = ?, status = 'active',
           resubmit_count = resubmit_count + 1, updated_at = datetime('now') WHERE id = ?""",
        (data_json, values["reason"], proposal_id))
    db.execute("UPDATE forum_posts SET updated_at = datetime('now') WHERE id = ?",
               (p["forum_post_id"],))
    auth.audit("proposal_resubmit", "card_proposal", proposal_id)
    # 通知管理员有提案等待复审
    from .notify import notify
    new_count = p["resubmit_count"] + 1
    for admin in db.query("SELECT id FROM users WHERE role = 'admin' AND is_banned = 0"):
        notify(admin["id"], user["id"], "proposal_resubmitted",
               forum_post_id=p["forum_post_id"],
               dedup_anchor=f"proposal-{proposal_id}-r{new_count}")
    flash("提案已重新提交，等待管理员审核（原投票分数保留）", "success")
    return redirect(url_for("forum.detail", post_id=p["forum_post_id"]))


# ---------- 管理员审核：批准（应用） / 不批准 / 打回 ----------

@bp.route("/proposals/<int:proposal_id>/review", methods=["POST"])
@auth.login_required
def review(proposal_id: int):
    auth.check_csrf()
    if not auth.is_admin():
        abort(403, description="只有管理员可以审核提案")
    me = auth.current_user()
    p = _proposal_or_404(proposal_id)
    if p["status"] not in ("active", "returned"):
        abort(403, description="该提案已归档，不能再审核")
    action = request.form.get("action", "")
    note = (request.form.get("note") or "").strip()[:500]
    if action not in ("approve", "reject", "return"):
        abort(400, description="未知的审核操作")
    if action == "return" and not note:
        flash("打回修改必须填写管理员备注，告知作者需要改什么", "danger")
        return redirect(url_for("forum.detail", post_id=p["forum_post_id"]))

    card = _card_row(p["card_id"])
    data = json.loads(p["data"] or "{}")
    before_json = None

    if action == "approve":
        before = {f: card.get(f) for f in EDITABLE_FIELDS}
        before_json = json.dumps(before, ensure_ascii=False)
        if p["type"] == "modification":
            # 修改提案：提案版本直接写入正式卡牌（slug 跟随名称去重）
            from . import cards as cards_mod
            slug = cards_mod._unique_slug(data["name"], exclude_id=card["id"])
            db.execute(
                """UPDATE cards SET slug=?, name=?, nation=?, type=?, unit_class=?,
                   cost_g=?, cost_z=?, cost_oil=?, attack=?, defense=?, vision_range=?,
                   attack_range=?, abilities=?, rarity=?, flavor_text=?, description=?,
                   updated_at=datetime('now') WHERE id=?""",
                (slug, data["name"], data["nation"], data["type"], data["unit_class"],
                 data["cost_g"], data["cost_z"], data["cost_oil"], data["attack"],
                 data["defense"], data["vision_range"], data["attack_range"],
                 data["abilities"], data["rarity"], data["flavor_text"],
                 data["description"], card["id"]))
            approve_detail = "修改已应用到正式卡牌"
        else:
            # 转正提案：玩家自制 → 官方（同一行卡牌改 source，不复制数据）
            from . import cards as cards_mod
            official_tag = cards_mod._official_tag(None)
            slug = cards_mod._unique_slug(data["name"], exclude_id=card["id"])
            db.execute(
                """UPDATE cards SET slug=?, name=?, nation=?, type=?, unit_class=?,
                   cost_g=?, cost_z=?, cost_oil=?, attack=?, defense=?, vision_range=?,
                   attack_range=?, abilities=?, rarity=?, flavor_text=?, description=?,
                   source='official', tag=?, updated_at=datetime('now') WHERE id=?""",
                (slug, data["name"], data["nation"], data["type"], data["unit_class"],
                 data["cost_g"], data["cost_z"], data["cost_oil"], data["attack"],
                 data["defense"], data["vision_range"], data["attack_range"],
                 data["abilities"], data["rarity"], data["flavor_text"],
                 data["description"], official_tag, card["id"]))
            approve_detail = "卡牌已转正为官方卡牌"
        new_status = "approved"
    elif action == "reject":
        new_status = "rejected"
        approve_detail = ""
    else:  # return
        new_status = "returned"
        approve_detail = ""

    db.execute(
        """UPDATE card_proposals SET status = ?, admin_note = ?, reviewed_by = ?,
           reviewed_at = datetime('now'), before_data = COALESCE(?, before_data),
           updated_at = datetime('now') WHERE id = ?""",
        (new_status, note, me["id"], before_json, proposal_id))
    # 批准 / 不批准 → 帖子自动归档（内容冻结，永久可查看）
    if new_status in ("approved", "rejected"):
        db.execute("UPDATE forum_posts SET status = 'archived', updated_at = datetime('now') "
                   "WHERE id = ?", (p["forum_post_id"],))

    auth.audit(f"proposal_{action}", "card_proposal", proposal_id,
               f"{PROPOSAL_TYPES.get(p['type'], p['type'])} #{proposal_id} "
               f"卡牌#{p['card_id']} → {PROPOSAL_STATUSES[new_status]}"
               + (f" 备注：{note}" if note else ""))

    from .notify import notify
    ntype = {"approve": "proposal_approved", "reject": "proposal_rejected",
             "return": "proposal_returned"}[action]
    notify(p["author_id"], me["id"], ntype,
           forum_post_id=p["forum_post_id"],
           detail=note or (approve_detail or None),
           dedup_anchor=f"proposal-{proposal_id}-{p['resubmit_count']}-{action}")

    done = {"approve": "已批准并应用", "reject": "未批准", "return": "已打回修改"}[action]
    flash(f"提案{done}" + ("，帖子已归档" if new_status in ("approved", "rejected") else ""),
          "success")
    return redirect(url_for("forum.detail", post_id=p["forum_post_id"]))
