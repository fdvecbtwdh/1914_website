"""服务器状态页（/status）与状态 API。
- 网站服务器：真实采集 OS / Python / 数据库 / 运行时长 / CPU / 内存 / GPU(nvidia-smi)
- 游戏服务器：占位区。游戏服务端按 heartbeat 协议推送数据（见游戏项目 docs/web-integration.md），
  未收到心跳或心跳过期（>60s）时显示"不可用"。
"""
import hmac
import platform
import re
import subprocess
import time

from flask import Blueprint, current_app, jsonify, render_template, request

from . import db, auth

bp = Blueprint("serverstatus", __name__)

GAME_HEARTBEAT_TTL = 60  # 秒，超过视为游戏服不可用
_gpu_cache = {"at": 0.0, "data": None}


def _gpu_status(refresh=False) -> dict:
    """通过 nvidia-smi 采集 GPU；不可用/超时返回 available=False。缓存 15 秒。"""
    now = time.time()
    if not refresh and now - _gpu_cache["at"] < 15:
        return _gpu_cache["data"]
    info = {"available": False, "name": "", "util": None, "mem_used": None, "mem_total": None}
    try:
        out = subprocess.run(
            ["nvidia-smi",
             "--query-gpu=name,utilization.gpu,memory.used,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5)
        if out.returncode == 0 and out.stdout.strip():
            parts = [p.strip() for p in out.stdout.strip().splitlines()[0].split(",")]
            info = {"available": True, "name": parts[0],
                    "util": _num(parts[1]), "mem_used": _num(parts[2]), "mem_total": _num(parts[3])}
    except Exception:
        pass
    _gpu_cache["at"] = now
    _gpu_cache["data"] = info
    return info


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _web_status() -> dict:
    import psutil
    cfg = current_app.config
    proc = psutil.Process()
    db_ok, db_size = True, 0
    try:
        db.query("SELECT 1")
        db_size = int(__import__("os").path.getsize(cfg["DB_PATH"]))
    except Exception:
        db_ok = False
    vm = psutil.virtual_memory()
    return {
        "status": "up",
        "os": platform.platform(),
        "python": platform.python_version(),
        "server": "waitress",
        "app_version": cfg["GAME_VERSION"],
        "uptime_sec": int(time.time() - current_app.extensions["started_at"]),
        "cpu_pct": psutil.cpu_percent(interval=None),
        "cpu_cores": psutil.cpu_count(logical=True),
        "mem_used_gb": round((vm.total - vm.available) / 1024 ** 3, 2),
        "mem_total_gb": round(vm.total / 1024 ** 3, 2),
        "mem_pct": vm.percent,
        "gpu": _gpu_status(),
        "db": "ok" if db_ok else "error",
        "db_size_mb": round(db_size / 1024 ** 2, 2),
    }


def _game_status() -> dict:
    state = current_app.extensions.get("game_server")
    fresh = bool(state) and (time.time() - state["last_seen"] <= GAME_HEARTBEAT_TTL)
    base = {"available": False, "status": "不可用", "matches": None, "players": None,
            "cpu": None, "mem": None, "version": "", "last_seen": None,
            "heartbeat_ttl": GAME_HEARTBEAT_TTL,
            "token_configured": bool(current_app.config["GAME_SERVER_TOKEN"])}
    if fresh:
        base.update({
            "available": True,
            "status": "在线" if state["status"] == "online" else state["status"],
            "matches": state["matches"], "players": state["players"],
            "cpu": state["cpu"], "mem": state["mem"],
            "version": state["version"],
            "last_seen": int(state["last_seen"]),
            "last_seen_str": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(state["last_seen"])),
        })
    return base


@bp.route("/status")
def status_page():
    if not auth.is_moderator() and not current_app.config.get("STATUS_PUBLIC", True):
        auth.abort(403)
    return render_template("status.html", web=_web_status(), game=_game_status(),
                           ttl=GAME_HEARTBEAT_TTL)


@bp.route("/api/status")
def status_json():
    return jsonify({"web": _web_status(), "game": _game_status()})


@bp.route("/api/game-server/heartbeat", methods=["POST"])
def game_heartbeat():
    """游戏服务端心跳上报。协议见游戏项目 docs/web-integration.md。
    Header: X-Game-Token: <GAME_SERVER_TOKEN>
    Body(JSON): {status, matches, players, cpu, mem, version}
    """
    expected = current_app.config["GAME_SERVER_TOKEN"]
    if not expected:
        return jsonify({"ok": False,
                        "error": "heartbeat disabled: GAME_SERVER_TOKEN not configured"}), 403
    token = request.headers.get("X-Game-Token", "")
    if not token or not hmac.compare_digest(token, expected):
        return jsonify({"ok": False, "error": "invalid token"}), 403
    data = request.get_json(silent=True) or {}

    def _int(key):
        try:
            return max(0, int(data.get(key, 0)))
        except (TypeError, ValueError):
            return 0

    def _float(key):
        try:
            return max(0.0, min(100.0, float(data.get(key, 0))))
        except (TypeError, ValueError):
            return 0.0

    status = str(data.get("status", "online")).lower()
    if not re.fullmatch(r"[a-z]{0,20}", status):
        status = "online"
    current_app.extensions["game_server"] = {
        "last_seen": time.time(),
        "status": status,
        "matches": _int("matches"),
        "players": _int("players"),
        "cpu": _float("cpu"),
        "mem": _float("mem"),
        "version": str(data.get("version", ""))[:40],
    }
    return jsonify({"ok": True})
