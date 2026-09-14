"""卡牌社区 — 列表 / 搜索 / 详情 / 投稿 / 编辑 / 投稿预览。"""
import json

from flask import (Blueprint, abort, current_app, flash, make_response, redirect,
                   render_template, request, url_for)

from . import db, auth, interactions, uploads
from .gameconstants import (CARD_TYPES, UNIT_CLASSES, RARITIES, NATIONS, CARD_TAGS,
                            DEFAULT_CARD_TAG, official_tag_for_version,
                            ABILITIES, ability_name, ability_level)


def _official_tag(chosen: str | None) -> str:
    """官方卡性质：表单值仅在游戏 v1.0+ 时生效；v0.* 阶段一律测试卡。"""
    if official_tag_for_version(current_app.config["GAME_VERSION"]) != "正式":
        return "测试"
    return chosen if chosen in CARD_TAGS else DEFAULT_CARD_TAG

bp = Blueprint("cards", __name__, url_prefix="")

PAGE_SIZE = 24


def _parse_abilities(raw: str) -> list[str]:
    try:
        arr = json.loads(raw or "[]")
        return arr if isinstance(arr, list) else []
    except json.JSONDecodeError:
        return []


def decorate_card(row, with_meta: bool = False) -> dict:
    """Row → 模板友好的 dict（ abilities 解析、范围文案等）。"""
    d = dict(row)
    d["abilities_list"] = _parse_abilities(d.get("abilities", "[]"))
    d["type_label"] = CARD_TYPES.get(d.get("type"), d.get("type"))
    d["class_label"] = UNIT_CLASSES.get(d.get("unit_class"), d.get("unit_class") or "—")
    d["rarity_label"] = RARITIES.get(d.get("rarity"), d.get("rarity"))
    d["tag"] = (d.get("tag") or "").strip()
    d["tag_desc"] = CARD_TAGS.get(d["tag"], "") if d["tag"] else ""
    d["nation_label"] = NATIONS.get(d.get("nation"), d.get("nation"))
    if with_meta:
        d["vote_count"] = interactions.vote_count("card", d["id"])
        d["comment_count"] = interactions.comment_count("card", d["id"])
    return d


def _card_or_404(card_id: int) -> dict:
    row = db.query(
        """SELECT c.*, u.username AS author_name, u.role AS author_role FROM cards c
           LEFT JOIN users u ON u.id = c.author_id WHERE c.id = ?""", (card_id,), one=True)
    if row is None:
        abort(404)
    card = decorate_card(row, with_meta=True)
    card["author_name"] = row["author_name"]
    return card


def _validate_card_form(form) -> tuple[dict, str | None]:
    """校验投稿表单，返回 (fields, error)。"""
    name = (form.get("name") or "").strip()
    if not (1 <= len(name) <= 30):
        return {}, "卡牌名称需 1–30 字符"
    card_type = form.get("type") or "unit"
    if card_type not in CARD_TYPES:
        return {}, "无效的卡牌类型"
    unit_class = form.get("unit_class") or ""
    if unit_class and unit_class not in UNIT_CLASSES:
        return {}, "无效的兵种"
    if card_type == "unit" and not unit_class:
        return {}, "单位卡必须选择兵种"

    def _int(key, lo, hi, default=0):
        try:
            v = int(form.get(key, default))
        except (TypeError, ValueError):
            return default
        return max(lo, min(hi, v))

    fields = {
        "name": name,
        "type": card_type,
        "unit_class": unit_class,
        "nation": form.get("nation") if form.get("nation") in NATIONS else "neutral",
        "rarity": form.get("rarity") if form.get("rarity") in RARITIES else "common",
        "card_tag": form.get("card_tag") if form.get("card_tag") in CARD_TAGS else DEFAULT_CARD_TAG,
        "cost_g": _int("cost_g", 0, 999),
        "cost_z": _int("cost_z", 0, 99),
        "cost_oil": _int("cost_oil", 0, 99),
        "attack": _int("attack", 0, 99),
        "defense": _int("defense", 0, 99),
        "vision_range": (form.get("vision_range") or "").strip()[:60],
        "attack_range": (form.get("attack_range") or "").strip()[:60],
        "flavor_text": (form.get("flavor_text") or "").strip()[:200],
        "description": (form.get("description") or "").strip(),
    }
    if len(fields["description"]) > 10000:
        return {}, "卡牌描述过长"
    # abilities：多选框 + 等级
    abilities = []
    for token in form.getlist("abilities"):
        base = ability_name(token)
        if base in ABILITIES:
            lvl = form.get(f"ability_level_{base}", "")
            if base in ("坚守", "补给", "修复", "爆破") and lvl.isdigit() and int(lvl) > 1:
                abilities.append(f"{base}{int(lvl)}")
            elif base not in abilities:
                abilities.append(base)
    fields["abilities"] = json.dumps(abilities, ensure_ascii=False)
    return fields, None


def _unique_slug(name: str, exclude_id: int | None = None) -> str:
    import re
    import unicodedata
    base = re.sub(r"[^a-z0-9\-]+", "", unicodedata.normalize("NFKC", name.lower()).replace(" ", "-"))
    base = base or "card"
    slug, n = base, 1
    while True:
        row = db.query("SELECT id FROM cards WHERE slug = ?", (slug,), one=True)
        if row is None or (exclude_id is not None and row["id"] == exclude_id):
            return slug
        n += 1
        slug = f"{base}-{n}"


# ---------- 列表 ----------

CARD_VIEWS = ("grid", "compact", "list")


@bp.route("/cards")
def card_list():
    sort = request.args.get("sort", "latest")
    q = (request.args.get("q") or "").strip()
    card_type = request.args.get("type", "")
    unit_class = request.args.get("class", "")
    rarity = request.args.get("rarity", "")
    tag = request.args.get("tag", "")
    source = request.args.get("source", "")
    page = max(1, min(request.args.get("page", 1, type=int), 500))
    # 视图：显式参数优先，其次 Cookie 记住的偏好，默认卡片视图
    view_param = request.args.get("view")
    view = view_param if view_param in CARD_VIEWS else None
    view = view or request.cookies.get("cards_view") or "grid"

    where = ["c.status = 'visible'"]
    args: list = []
    card_tag = request.args.get("card_tag", "")
    if card_tag in CARD_TAGS:
        where.append("c.tag = ?")
        args.append(card_tag)
    if source in ("official", "community"):
        where.append("c.source = ?")
        args.append(source)
    if q:
        where.append("(c.name LIKE ? OR c.flavor_text LIKE ? OR c.description LIKE ?)")
        like = f"%{q}%"
        args += [like, like, like]
    if card_type in CARD_TYPES:
        where.append("c.type = ?")
        args.append(card_type)
    if unit_class in UNIT_CLASSES:
        where.append("c.unit_class = ?")
        args.append(unit_class)
    if rarity in RARITIES:
        where.append("c.rarity = ?")
        args.append(rarity)
    if tag:
        where.append(
            "c.id IN (SELECT content_id FROM content_labels cl JOIN labels l ON l.id = cl.label_id "
            "WHERE cl.content_type = 'card' AND l.name = ?)")
        args.append(tag)

    where_sql = " AND ".join(where)
    order = {
        "hot": "vote_count DESC, c.created_at DESC",
        "discussed": "comment_count DESC, c.created_at DESC",
    }.get(sort, "c.created_at DESC, c.id DESC")

    base_sql = f"""FROM cards c LEFT JOIN users u ON u.id = c.author_id
        LEFT JOIN (SELECT target_id, COUNT(*) vc FROM votes WHERE target_type='card' GROUP BY target_id) v
          ON v.target_id = c.id
        LEFT JOIN (SELECT target_id, COUNT(*) cc FROM comments WHERE target_type='card' AND is_deleted=0
          GROUP BY target_id) cm ON cm.target_id = c.id
        WHERE {where_sql}"""
    total = db.query(f"SELECT COUNT(*) AS n {base_sql}", tuple(args), one=True)["n"]
    rows = db.query(
        f"""SELECT c.*, u.username AS author_name, IFNULL(v.vc, 0) AS vote_count,
            IFNULL(cm.cc, 0) AS comment_count {base_sql}
            ORDER BY {order} LIMIT ? OFFSET ?""",
        (*args, PAGE_SIZE, (page - 1) * PAGE_SIZE))

    cards = [decorate_card(r) for r in rows]
    voted_ids = interactions.user_votes_map(
        auth.current_user()["id"] if auth.is_logged_in() else 0, "card",
        [c["id"] for c in cards])
    for c in cards:
        c["my_vote"] = c["id"] in voted_ids

    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    resp = make_response(render_template(
        "cards/list.html", cards=cards, total=total, page=page, pages=pages,
        sort=sort, q=q, card_type=card_type, unit_class=unit_class,
        rarity=rarity, tag=tag, source=source, view=view, card_tag=card_tag,
        card_types=CARD_TYPES, unit_classes=UNIT_CLASSES, rarities=RARITIES,
        hot_tags=_hot_tags()))
    if view_param in CARD_VIEWS:
        resp.set_cookie("cards_view", view, max_age=30 * 86400, samesite="Lax")
    return resp


def _hot_tags(limit: int = 12) -> list:
    return db.query(
        """SELECT l.name, l.color, COUNT(*) AS n FROM content_labels cl
           JOIN labels l ON l.id = cl.label_id
           WHERE cl.content_type = 'card'
           GROUP BY l.id ORDER BY n DESC LIMIT ?""", (limit,))


# ---------- 投稿 ----------

@bp.route("/cards/new", methods=["GET", "POST"])
@auth.login_required
def card_new():
    if request.method == "POST":
        auth.check_csrf()
        user = auth.current_user()
        mute = auth.active_mute(user)
        if mute:
            flash(auth.mute_notice(mute), "danger")
            return redirect(url_for("misc.home"))
        fields, err = _validate_card_form(request.form)
        if err:
            flash(err, "danger")
        else:
            # 来源：仅管理员可投官方卡，其他人一律强制 community
            user = auth.current_user()
            source = "official" if (user["role"] == "admin"
                                    and request.form.get("source") == "official") else "community"
            art_url = ""
            try:
                if request.files.get("art") and request.files["art"].filename:
                    art_url = uploads.save_card_image(request.files["art"])
            except ValueError as e:
                flash(str(e), "danger")
                return render_template("cards/form.html", card=None, editing=False,
                                       card_types=CARD_TYPES, unit_classes=UNIT_CLASSES,
                                       rarities=RARITIES, nations=NATIONS,
                                       abilities=ABILITIES,
                                       form_values=request.form), 400
            slug = _unique_slug(fields["name"])
            card_id = db.execute(
                """INSERT INTO cards (slug, name, nation, type, unit_class, cost_g, cost_z, cost_oil,
                   attack, defense, vision_range, attack_range, abilities, rarity, art_path, flavor_text,
                   description, author_id, source, status, tag)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'visible',?)""",
                (slug, fields["name"], fields["nation"], fields["type"], fields["unit_class"],
                 fields["cost_g"], fields["cost_z"], fields["cost_oil"], fields["attack"],
                 fields["defense"],
                 fields["vision_range"], fields["attack_range"], fields["abilities"],
                 fields["rarity"], art_url, fields["flavor_text"], fields["description"],
                 user["id"], source,
                 _official_tag(fields["card_tag"]) if source == "official" else ""))
            _set_labels("card", card_id, request.form.get("tags", ""))
            auth.audit("card_create", "card", card_id, fields["name"])
            flash("官方卡牌投稿成功！" if source == "official" else "卡牌投稿成功！", "success")
            return redirect(url_for("cards.card_detail", card_id=card_id))
    return render_template("cards/form.html", card=None, editing=False,
                           card_types=CARD_TYPES, unit_classes=UNIT_CLASSES,
                           rarities=RARITIES, nations=NATIONS, abilities=ABILITIES,
                           form_values={})


def _set_labels(content_type: str, content_id: int, tags_raw: str) -> None:
    """投稿/编辑时解析逗号分隔标签（自动创建，最多 6 个）。"""
    db.execute("DELETE FROM content_labels WHERE content_type = ? AND content_id = ?",
               (content_type, content_id))
    names = [t.strip() for t in (tags_raw or "").replace("，", ",").split(",") if t.strip()]
    for name in list(dict.fromkeys(names))[:6]:
        name = name[:30]
        row = db.query("SELECT id FROM labels WHERE name = ?", (name,), one=True)
        lid = row["id"] if row else db.execute(
            "INSERT INTO labels (name) VALUES (?)", (name,))
        db.execute("INSERT OR IGNORE INTO content_labels (content_type, content_id, label_id) "
                   "VALUES (?,?,?)", (content_type, content_id, lid))


# ---------- 详情 ----------

@bp.route("/card/<int:card_id>")
def card_detail(card_id: int):
    card = _card_or_404(card_id)
    if card["status"] != "visible" and not auth.is_moderator():
        abort(404)
    if card["status"] == "visible":
        db.execute("UPDATE cards SET view_count = view_count + 1 WHERE id = ?", (card_id,))
    card["labels"] = db.query(
        """SELECT l.id, l.name, l.color FROM content_labels cl JOIN labels l ON l.id = cl.label_id
           WHERE cl.content_type = 'card' AND cl.content_id = ?""", (card_id,))
    comments = interactions.comments_for("card", card_id)
    my_vote = interactions.user_has_voted(auth.current_user()["id"], "card", card_id) \
        if auth.is_logged_in() else False
    # 该卡的活跃提案数（修改提案针对官方卡；转正提案针对玩家自制卡）
    proposal_count = db.query(
        """SELECT COUNT(*) AS n FROM card_proposals
           WHERE card_id = ? AND status IN ('active','returned')""",
        (card_id,), one=True)["n"]
    proposal_category = "卡牌修改提案" if card["source"] == "official" else "卡牌转正"
    return render_template("cards/detail.html", card=card, comments=comments,
                           my_vote=my_vote, GAME_ABILITIES=ABILITIES,
                           proposal_count=proposal_count,
                           proposal_category=proposal_category)


# ---------- 编辑 / 删除 ----------

@bp.route("/card/<int:card_id>/edit", methods=["GET", "POST"])
@auth.login_required
def card_edit(card_id: int):
    row = db.query("SELECT * FROM cards WHERE id = ?", (card_id,), one=True)
    if row is None:
        abort(404)
    user = auth.current_user()
    is_admin = user["role"] == "admin"
    if row["source"] == "official":
        # 官方卡：仅管理员可在网站编辑（游戏 JSON 导入的卡，导入会同步覆盖游戏侧字段）
        if not is_admin:
            abort(403, description="官方卡牌只有管理员可以修改")
    elif row["author_id"] != user["id"] and not auth.is_moderator():
        abort(403)
    if request.method == "POST":
        auth.check_csrf()
        mute = auth.active_mute(user)
        if mute:
            flash(auth.mute_notice(mute), "danger")
            return redirect(f"/card/{card_id}")
        fields, err = _validate_card_form(request.form)
        if err:
            flash(err, "danger")
        else:
            art_url = row["art_path"]
            try:
                if request.files.get("art") and request.files["art"].filename:
                    art_url = uploads.save_card_image(request.files["art"])
            except ValueError as e:
                flash(str(e), "danger")
                return render_template("cards/form.html", card=dict(row), editing=True,
                                       card_types=CARD_TYPES, unit_classes=UNIT_CLASSES,
                                       rarities=RARITIES, nations=NATIONS, abilities=ABILITIES,
                                       form_values=request.form), 400
            # 来源仅在管理员编辑时可变更（如将自制卡采纳为官方）
            new_source = row["source"]
            if is_admin and request.form.get("source") in ("official", "community"):
                new_source = request.form["source"]
            # 性质仅对官方卡有意义：自制卡清空；官方卡取表单值（v0.* 阶段强制测试）
            if new_source == "official":
                fields["card_tag"] = _official_tag(request.form.get("card_tag"))
            else:
                fields["card_tag"] = ""
            slug = _unique_slug(fields["name"], exclude_id=card_id)
            db.execute(
                """UPDATE cards SET slug=?, name=?, nation=?, type=?, unit_class=?, cost_g=?, cost_z=?,
                   cost_oil=?, attack=?, defense=?, vision_range=?, attack_range=?, abilities=?, rarity=?,
                   art_path=?, flavor_text=?, description=?, source=?, tag=?, updated_at=datetime('now')
                   WHERE id=?""",
                (slug, fields["name"], fields["nation"], fields["type"], fields["unit_class"],
                 fields["cost_g"], fields["cost_z"], fields["cost_oil"], fields["attack"], fields["defense"],
                 fields["vision_range"], fields["attack_range"], fields["abilities"],
                 fields["rarity"], art_url, fields["flavor_text"], fields["description"],
                 new_source, fields.get("card_tag", row["tag"]), card_id))
            _set_labels("card", card_id, request.form.get("tags", ""))
            auth.audit("card_edit", "card", card_id, fields["name"])
            flash("卡牌已更新", "success")
            return redirect(url_for("cards.card_detail", card_id=card_id))
    card = decorate_card(dict(row))  # abilities_list 等（编辑表单回填词条勾选）
    card["labels"] = db.query(
        """SELECT l.name FROM content_labels cl JOIN labels l ON l.id = cl.label_id
           WHERE cl.content_type = 'card' AND cl.content_id = ?""", (card_id,))
    return render_template("cards/form.html", card=card, editing=True,
                           card_types=CARD_TYPES, unit_classes=UNIT_CLASSES,
                           rarities=RARITIES, nations=NATIONS, abilities=ABILITIES,
                           form_values=None,
                           tags=", ".join(l["name"] for l in card["labels"]))


@bp.route("/card/<int:card_id>/delete", methods=["POST"])
@auth.login_required
def card_delete(card_id: int):
    auth.check_csrf()
    row = db.query("SELECT * FROM cards WHERE id = ?", (card_id,), one=True)
    if row is None:
        abort(404)
    user = auth.current_user()
    if row["source"] == "official":
        if user["role"] != "admin":
            abort(403, description="官方卡牌只有管理员可以删除")
    elif row["author_id"] != user["id"] and not auth.is_moderator():
        abort(403)
    db.execute("DELETE FROM cards WHERE id = ?", (card_id,))
    auth.audit("card_delete", "card", card_id, row["name"])
    flash("卡牌已删除", "success")
    return redirect(url_for("cards.card_list"))
