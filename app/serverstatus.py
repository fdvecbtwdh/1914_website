"""服务器状态页（/status）与状态 API。
- 网站服务器：真实采集 OS / Python / 数据库 / 系统运行时长 / CPU / 内存 / GPU(nvidia-smi)
- 游戏服务器：占位区。游戏服务端按 heartbeat 协议推送数据（见游戏项目 docs/web-integration.md），
  未收到上报或数据过期（>60s）时显示"不可用"。

采集策略：网站启动时采样一次，后台线程每 30 秒更新快照；
页面与 API 只读快照——访问量大小不影响采集频率。
"""
import hmac
import platform
import re
import subprocess
import threading
import time

from flask import Blueprint, current_app, jsonify, render_template, request

from . import db, auth

bp = Blueprint("serverstatus", __name__)

GAME_HEARTBEAT_TTL = 60  # 秒，超过视为游戏服不可用
SAMPLE_INTERVAL = 30     # 秒，后台采样周期
_gpu_cache = {"at": 0.0, "data": None}
_cpu_model_cache = None
_snapshot_lock = threading.Lock()
_snapshot = {"at": 0.0, "web": None}


def _cpu_model() -> str:
    """CPU 型号（缓存，基本不变）。Windows 读注册表，其他平台读 /proc/cpuinfo。"""
    global _cpu_model_cache
    if _cpu_model_cache:
        return _cpu_model_cache
    model = ""
    try:
        if platform.system() == "Windows":
            import winreg
            with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                r"HARDWARE\DESCRIPTION\System\CentralProcessor\0") as key:
                model = str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
        else:
            with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as f:
                for line in f:
                    if "model name" in line:
                        model = line.split(":", 1)[1].strip()
                        break
    except Exception:
        pass
    if not model:
        model = platform.processor() or "未知"
    _cpu_model_cache = re.sub(r"\s+", " ", model)
    return _cpu_model_cache


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
    """返回最近一次采样的网站指标快照；快照不存在时（如测试环境）即时采集一次。"""
    if _snapshot["web"] is not None:
        return _snapshot["web"]
    return collect_now()


def collect_now() -> dict:
    """立即采样一次并更新快照。"""
    with _snapshot_lock:
        web = _collect()
        _snapshot["at"] = time.time()
        _snapshot["web"] = web
        return web


def _collect() -> dict:
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
    disk = psutil.disk_usage(str(cfg["UPLOAD_DIR"]))
    boot = psutil.boot_time()
    return {
        "status": "up",
        "os": platform.platform(),
        "python": platform.python_version(),
        "server": "waitress",
        "app_version": cfg["GAME_VERSION"],
        # 系统开机时长（非网站进程启动时长）
        "uptime_sec": int(time.time() - psutil.boot_time()),
        "boot_time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(boot)),
        "cpu_model": _cpu_model(),
        "cpu_pct": psutil.cpu_percent(interval=None),
        "cpu_cores": psutil.cpu_count(logical=True),
        "mem_used_gb": round((vm.total - vm.available) / 1024 ** 3, 2),
        "mem_total_gb": round(vm.total / 1024 ** 3, 2),
        "mem_pct": vm.percent,
        "disk_used_gb": round(disk.used / 1024 ** 3, 2),
        "disk_total_gb": round(disk.total / 1024 ** 3, 2),
        "disk_pct": disk.percent,
        "gpu": _gpu_status(),
        "db": "ok" if db_ok else "error",
        "db_size_mb": round(db_size / 1024 ** 2, 2),
    }


def _game_status() -> dict:
    state = current_app.extensions.get("game_server")
    fresh = bool(state) and (time.time() - state["last_seen"] <= GAME_HEARTBEAT_TTL)
    base = {"available": False, "status": "不可用", "matches": None, "players": None,
            "max_matches": None, "max_players": None,
            "cpu": None, "mem": None, "version": "", "last_seen": None,
            "last_seen_str": "",
            "heartbeat_ttl": GAME_HEARTBEAT_TTL,
            "token_configured": bool(current_app.config["GAME_SERVER_TOKEN"])}
    if fresh:
        base.update({
            "available": True,
            "status": "在线" if state["status"] == "online" else state["status"],
            "matches": state["matches"], "players": state["players"],
            "max_matches": state["max_matches"], "max_players": state["max_players"],
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
    web = _web_status()
    game = _game_status()
    return render_template("status.html", web=web, game=game, ttl=GAME_HEARTBEAT_TTL)


@bp.route("/api/status")
def status_json():
    return jsonify({"web": _web_status(), "game": _game_status()})


def start_sampler(app) -> None:
    """启动后台采样线程：立即采样一次，之后每 30 秒更新快照（daemon，随进程退出）。"""
    def loop():
        while True:
            try:
                with app.app_context():
                    collect_now()
            except Exception:
                pass
            time.sleep(SAMPLE_INTERVAL)
    threading.Thread(target=loop, name="status-sampler", daemon=True).start()


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
        "max_matches": _int("max_matches"),
        "max_players": _int("max_players"),
        "cpu": _float("cpu"),
        "mem": _float("mem"),
        "version": str(data.get("version", ""))[:40],
    }
    return jsonify({"ok": True})
