"""导航页（/nav）— 站点枢纽页。
以后会在这里陆续添加其他内容；目前只有「去 1914」一个入口。
要加新条目：在 NAV_ITEMS 里追加一个元组即可（图标, 标题, 描述, 链接）。
"""
from flask import Blueprint, render_template

bp = Blueprint("nav", __name__)

# (图标, 标题, 描述, 链接) —— 之后有新东西就在这里加
NAV_ITEMS = [
    ("✠", "1914", "WW1 卡牌对战游戏 · 官方网站 / 卡牌社区 / Bug 反馈", "/index"),
    ("🎮", "1914 仓库", "游戏源码 · GitHub（1914）", "https://github.com/fdvecbtwdh/1914"),
    ("🐙", "网页仓库", "本站源码 · GitHub（1914_website）",
     "https://github.com/fdvecbtwdh/1914_website"),
]


@bp.route("/nav")
def nav_page():
    return render_template("nav.html", items=NAV_ITEMS)
