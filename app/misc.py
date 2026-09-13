"""杂项路由 — 首页 / 关于 / 统一搜索 / 上传文件服务 / SEO (sitemap, robots) / 报告处理。"""
import time

from flask import (Blueprint, abort, render_template, request, send_from_directory,
                   current_app, Response)

from . import db, auth, interactions
from .gameconstants import ISSUE_STATUSES

bp = Blueprint("misc", __name__)


@bp.route("/")
def home_redirect():
    """根路径永久重定向到 /index（外链与搜索引擎友好）。"""
    from flask import redirect, url_for
    return redirect(url_for("misc.home"), code=301)


@bp.route("/index")
def home():
    # 最新 / 热门卡牌
    latest_cards = db.query(
        """SELECT c.*, u.username AS author_name FROM cards c
           LEFT JOIN users u ON u.id = c.author_id
           WHERE c.status = 'visible' ORDER BY c.created_at DESC, c.id DESC LIMIT 4""")
    hot_cards = db.query(
        """SELECT c.*, u.username AS author_name, IFNULL(v.vc,0) AS vote_count FROM cards c
           LEFT JOIN users u ON u.id = c.author_id
           LEFT JOIN (SELECT target_id, COUNT(*) vc FROM votes WHERE target_type='card'
             GROUP BY target_id) v ON v.target_id = c.id
           WHERE c.status = 'visible' ORDER BY vote_count DESC, c.created_at DESC LIMIT 4""")
    # 最新未完成 Issue
    latest_issues = db.query(
        """SELECT i.*, u.username AS author_name, u.role AS author_role FROM issues i
           LEFT JOIN users u ON u.id = i.author_id
           WHERE i.status IN ('open','in_progress')
           ORDER BY i.created_at DESC LIMIT 5""")
    # 社区统计
    stats = {
        "cards": db.query("SELECT COUNT(*) AS n FROM cards WHERE status='visible'", one=True)["n"],
        "issues": db.query("SELECT COUNT(*) AS n FROM issues", one=True)["n"],
        "users": db.query("SELECT COUNT(*) AS n FROM users", one=True)["n"],
        "open_issues": db.query(
            "SELECT COUNT(*) AS n FROM issues WHERE status IN ('open','in_progress')",
            one=True)["n"],
    }
    return render_template("home.html", latest_cards=latest_cards, hot_cards=hot_cards,
                           latest_issues=latest_issues, stats=stats)


@bp.route("/about")
def about():
    return render_template("about.html")


@bp.route("/search")
def search():
    q = (request.args.get("q") or "").strip()
    cards, issues, users_found, forum_posts = [], [], [], []
    if q:
        like = f"%{q}%"
        cards = db.query(
            """SELECT c.*, u.username AS author_name, IFNULL(v.vc,0) AS vote_count FROM cards c
               LEFT JOIN users u ON u.id = c.author_id
               LEFT JOIN (SELECT target_id, COUNT(*) vc FROM votes WHERE target_type='card'
                 GROUP BY target_id) v ON v.target_id = c.id
               WHERE c.status='visible' AND
                 (c.name LIKE ? OR c.flavor_text LIKE ? OR c.description LIKE ?)
               ORDER BY vote_count DESC, c.created_at DESC LIMIT 12""",
            (like, like, like))
        issues = db.query(
            """SELECT i.*, u.username AS author_name FROM issues i
               LEFT JOIN users u ON u.id = i.author_id
               WHERE i.title LIKE ? OR i.body LIKE ?
               ORDER BY i.created_at DESC LIMIT 12""", (like, like))
        forum_posts = db.query(
            """SELECT f.id, f.title, f.category, f.created_at FROM forum_posts f
               WHERE f.status = 'visible' AND (f.title LIKE ? OR f.body LIKE ?)
               ORDER BY f.created_at DESC LIMIT 12""", (like, like))
        users_found = db.query(
            """SELECT u.id, u.username, u.role, u.bio, u.created_at,
               (SELECT COUNT(*) FROM cards c WHERE c.author_id = u.id AND c.status='visible') AS card_count
               FROM users u WHERE u.username LIKE ? AND u.is_banned = 0 LIMIT 10""", (like,))
    return render_template("search.html", q=q, cards=cards, issues=issues,
                           forum_posts=forum_posts, users=users_found)


# ---------- 静态上传文件 ----------

@bp.route("/uploads/<path:filename>")
def uploads_file(filename: str):
    return send_from_directory(str(current_app.config["UPLOAD_DIR"]), filename)


# ---------- SEO ----------

@bp.route("/robots.txt")
def robots_txt():
    return Response(
        f"User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /settings\n"
        f"Sitemap: {current_app.config['SITE_URL']}/sitemap.xml\n",
        mimetype="text/plain")


@bp.route("/sitemap.xml")
def sitemap():
    base = current_app.config["SITE_URL"]
    urls = [(f"{base}/index", "1.0"), (f"{base}/about", "0.6"),
            (f"{base}/cards", "0.9"), (f"{base}/issues", "0.9")]
    for r in db.query("SELECT id, updated_at FROM cards WHERE status='visible' "
                      "ORDER BY id DESC LIMIT 2000"):
        urls.append((f"{base}/card/{r['id']}", "0.7"))
    for r in db.query("SELECT id, updated_at FROM issues ORDER BY id DESC LIMIT 2000"):
        urls.append((f"{base}/issue/{r['id']}", "0.7"))
    for r in db.query("SELECT id, updated_at FROM forum_posts WHERE status='visible' "
                      "ORDER BY id DESC LIMIT 2000"):
        urls.append((f"{base}/forum/{r['id']}", "0.7"))
    xml_items = []
    for loc, pri in urls:
        xml_items.append(f"<url><loc>{loc}</loc><priority>{pri}</priority></url>")
    xml = ('<?xml version="1.0" encoding="UTF-8"?>\n'
           '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">'
           + "".join(xml_items) + "</urlset>")
    return Response(xml, mimetype="application/xml")
