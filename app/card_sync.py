"""官方卡牌导入 — 读取游戏项目 data/cards/**/*.json，与网站卡牌表同步。
游戏是数据源头：游戏里改了卡牌 JSON，重跑 import_official_cards() 即可。
"""
import json
import re
import unicodedata
from pathlib import Path

from flask import current_app

from . import db

ALLOWED_FIELDS = {
    "id", "name", "nation", "type", "unit_class", "cost_g", "cost_z",
    "attack", "defense", "vision_range", "attack_range", "abilities",
    "rarity", "art", "flavor_text",
}


def _slugify(name: str, fallback: str) -> str:
    """中文/英文混合名 → URL 友好 slug。"""
    name = (name or "").strip().lower().replace(" ", "-")
    name = unicodedata.normalize("NFKC", name)
    name = re.sub(r"[^a-z0-9\-]+", "", name)
    return name or fallback


def load_game_cards(game_path: str | None = None) -> list[dict]:
    """读取游戏项目全部卡牌 JSON。
    游戏项目不存在或没有卡牌时，回退到项目内置 seed/cards/ 副本，
    保证无游戏项目的部署环境（如独立服务器）也能导入官方卡。
    """
    if game_path is None:
        if current_app:
            game_path = current_app.config["GAME_PROJECT_PATH"]
        else:
            from .config import Config
            game_path = Config.GAME_PROJECT_PATH
    cards: list[dict] = []
    if game_path:
        root = Path(game_path) / "data" / "cards"
        if root.is_dir():
            for sub in ("units", "orders"):
                d = root / sub
                if not d.is_dir():
                    continue
                for f in sorted(d.glob("*.json")):
                    data = _read_card_json(f)
                    if data is not None:
                        data.setdefault("type", "order" if sub == "orders" else "unit")
                        cards.append(data)
    if not cards:
        seed = Path(__file__).resolve().parent.parent / "seed" / "cards"
        for f in sorted(seed.glob("*.json")):
            data = _read_card_json(f)
            if data is not None:
                data.setdefault("type", "unit")
                cards.append(data)
    return cards


def _read_card_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or not data.get("id"):
        return None
    return {k: v for k, v in data.items() if k in ALLOWED_FIELDS}


def import_official_cards(game_path: str | None = None) -> dict:
    """把游戏 JSON 导入/更新到网站 cards 表（source='official'）。
    返回 {created: n, updated: n, skipped: n}。
    """
    created = updated = skipped = 0
    for data in load_game_cards(game_path):
        gid = str(data["id"])
        name = str(data.get("name") or gid)
        abilities = data.get("abilities") or []
        if not isinstance(abilities, list):
            abilities = []
        existing = db.query("SELECT id FROM cards WHERE game_id = ?", (gid,), one=True)
        # 卡牌性质：ID/名称含测试标记 → 测试卡，否则正式
        tag = "测试" if ("test" in gid.lower() or "测试" in name) else "正式"
        if existing:
            db.execute(
                """UPDATE cards SET name=?, nation=?, type=?, unit_class=?, cost_g=?, cost_z=?,
                   attack=?, defense=?, vision_range=?, attack_range=?, abilities=?, rarity=?,
                   flavor_text=?, tag=?, updated_at=datetime('now') WHERE id=?""",
                (name, data.get("nation") or "neutral", data.get("type") or "unit",
                 data.get("unit_class") or "", int(data.get("cost_g") or 0),
                 int(data.get("cost_z") or 0), int(data.get("attack") or 0),
                 int(data.get("defense") or 0), data.get("vision_range") or "",
                 data.get("attack_range") or "", json.dumps(abilities, ensure_ascii=False),
                 data.get("rarity") or "common", data.get("flavor_text") or "",
                 tag, existing["id"]),
            )
            updated += 1
        else:
            slug = _slugify(name, gid)
            base, n = slug, 1
            while db.query("SELECT 1 FROM cards WHERE slug = ?", (slug,), one=True):
                n += 1
                slug = f"{base}-{n}"
            db.execute(
                """INSERT INTO cards (game_id, slug, name, nation, type, unit_class, cost_g, cost_z,
                   attack, defense, vision_range, attack_range, abilities, rarity, flavor_text,
                   description, author_id, source, status, tag)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,NULL,'official','visible',?)""",
                (gid, slug, name, data.get("nation") or "neutral", data.get("type") or "unit",
                 data.get("unit_class") or "", int(data.get("cost_g") or 0),
                 int(data.get("cost_z") or 0), int(data.get("attack") or 0),
                 int(data.get("defense") or 0), data.get("vision_range") or "",
                 data.get("attack_range") or "", json.dumps(abilities, ensure_ascii=False),
                 data.get("rarity") or "common", data.get("flavor_text") or "",
                 f"官方卡牌，来自游戏数据（`{gid}`）。", tag),
            )
            created += 1
    return {"created": created, "updated": updated, "skipped": skipped}
