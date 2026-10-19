r"""恩尼格玛密码机教学页（/enigma）——独立静态站点。

构建产物位于 E:\#Code\1914_website\enigma_site（Vite base=/enigma/）。
本模块只暴露导航入口与静态文件，不依赖本站数据库、模板或登录状态。
"""
from pathlib import Path

from flask import Blueprint, redirect, send_from_directory

bp = Blueprint("enigma", __name__)

SITE_DIR = Path(__file__).resolve().parent.parent / "enigma_site"


@bp.route("/enigma")
def enigma_bare():
    """补上结尾斜杠，避免相对资源路径算错。"""
    return redirect("/enigma/", code=302)


@bp.route("/enigma/")
def enigma_index():
    return send_from_directory(SITE_DIR, "index.html")


@bp.route("/enigma/<path:filename>")
def enigma_file(filename: str):
    return send_from_directory(SITE_DIR, filename)
