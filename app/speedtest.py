"""网络测速（/speedtest）— 访客设备到本站服务器的链路实测。

测的是完整链路：设备 → Cloudflare 边缘 → Cloudflare Tunnel → 本站 waitress，
不是访客本地宽带的完整测速（页面里有明确说明）。

接口（无状态、无数据库写入）：
- GET  /api/speedtest/ping        微型 JSON，探测 RTT（页面连发 10 次取最小/平均/抖动）
- GET  /api/speedtest/download    流式下发 incompressible 随机数据（?size= 字节，服务端钳制）
- POST /api/speedtest/upload      接收请求体并按块读入即丢弃，返回实收字节数

与全站限流的配合：前端按"每请求约 2 秒传输量"自适应取数据（上限 32MB），请求速率
≈ 0.5 次/秒，远低于全站每秒 30 请求限流；延迟探测为短促 10 连发，也在阈值内。

防滥用（内存记账，重启清零；不写数据库）：
- 每 IP 每小时下载/上传流量预算（SPEEDTEST_DL/UL_BUDGET_MB，超限 429）
- 延迟探测频率限制（SPEEDTEST_PING_LIMIT / 5 分钟，超限 429）
- 并发数据传输上限（SPEEDTEST_MAX_CONCURRENT，超限 503，给正常访问留 waitress 线程）
- 单请求字节数钳制 32 MB（下载/上传同），叠加全站每秒 30 请求节流

上传走 /api/ 前缀，已登录会话与 /api 其余写接口一样强制 CSRF（游客无会话跳过，
由前端统一带 X-CSRF-Token 头）。功能可用 SPEEDTEST_ENABLED=0 整体关闭（404）。
"""
import json
import os
import threading
import time

from flask import Blueprint, Response, current_app, jsonify, render_template, request

bp = Blueprint("speedtest", __name__)

DL_REQ_MAX = 32 * 1024 * 1024  # 单次下载请求最大字节数
UL_REQ_MAX = 32 * 1024 * 1024  # 单次上传请求最大字节数（浏览器端自适应请求约 2 秒传输量，
                               # 请求速率 ≈ 0.5/s，与全站每秒 30 请求限流保持安全距离）
DL_MIN = 64 * 1024             # 单次下载请求最小字节数
READ_CHUNK = 64 * 1024         # 上传读取块大小
PING_WINDOW_MIN = 5            # ping 频率限制窗口（分钟）
BUDGET_WINDOW_SEC = 3600       # 流量预算窗口（秒，固定窗口）

# 进程内测速载荷：随机数据不可压缩，避免中间层压缩让结果虚高；8MB 一次生成反复复用
_PAYLOAD = b""
_PAYLOAD_LOCK = threading.Lock()

_state_lock = threading.Lock()
_budgets: dict[str, list] = {}   # ip -> [窗口起点, 已用下载字节, 已用上传字节]
_pings: dict[str, list] = {}     # ip -> [窗口起点, 次数]
_concurrent = 0                  # 正在进行的下载/上传数据请求数


def reset_state() -> None:
    """清空内存记账（测试隔离用）。"""
    global _concurrent
    with _state_lock:
        _budgets.clear()
        _pings.clear()
        _concurrent = 0


def _get_payload() -> bytes:
    global _PAYLOAD
    if not _PAYLOAD:
        with _PAYLOAD_LOCK:
            if not _PAYLOAD:
                _PAYLOAD = os.urandom(DL_REQ_MAX)
    return _PAYLOAD


def _ip() -> str:
    from .security import client_ip
    return client_ip()


def _enabled() -> bool:
    return bool(current_app.config.get("SPEEDTEST_ENABLED", True))


def _json_error(code: int, message: str):
    resp = jsonify({"ok": False, "error": message})
    resp.headers["Cache-Control"] = "no-store"
    return resp, code


@bp.before_request
def _gate():
    """功能总开关：SPEEDTEST_ENABLED=0 时页面与接口统一 404，如同不存在。"""
    if not _enabled():
        return "功能未开启", 404, {"Content-Type": "text/plain; charset=utf-8"}
    return None


@bp.before_request
def _csrf_protect():
    """与 api.py 同规则：已建立会话的写请求必须带有效 CSRF token。"""
    if request.method in ("POST", "PUT", "DELETE"):
        from flask import session
        good = session.get("csrf", "")
        if not good:
            return None  # 无会话（游客直连）→ 与 /api 一致，放行给视图
        import secrets as _secrets
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token", "")
        if not token or not _secrets.compare_digest(token, good):
            from .auth import CSRFError, _record_csrf_failure
            _record_csrf_failure("no_token" if not token else "token_mismatch")
            raise CSRFError("CSRF 校验失败，请刷新页面重试")
    return None


# ---------- 流量预算 / 频率 / 并发守卫 ----------

def _budget_take(ip: str, field: int, n_bytes: int) -> bool:
    """在小时窗口内为该 IP 记账 n 字节；超出预算返回 False。field: 1=下载 2=上传。"""
    cap_mb = current_app.config["SPEEDTEST_DL_BUDGET_MB" if field == 1
                                else "SPEEDTEST_UL_BUDGET_MB"]
    now = time.time()
    with _state_lock:
        entry = _budgets.get(ip)
        if entry is None or now - entry[0] >= BUDGET_WINDOW_SEC:
            if len(_budgets) > 5000:
                _budgets.clear()
            entry = [now, 0, 0]
            _budgets[ip] = entry
        cap = cap_mb * 1024 * 1024
        if entry[field] + n_bytes > cap:
            return False
        entry[field] += n_bytes
        return True


def _ping_allowed(ip: str) -> bool:
    now = time.time()
    with _state_lock:
        entry = _pings.get(ip)
        if entry is None or now - entry[0] >= PING_WINDOW_MIN * 60:
            if len(_pings) > 5000:
                _pings.clear()
            entry = [now, 0]
            _pings[ip] = entry
        entry[1] += 1
        return entry[1] <= current_app.config["SPEEDTEST_PING_LIMIT"]


def _concurrency_full() -> bool:
    return _concurrent >= current_app.config["SPEEDTEST_MAX_CONCURRENT"]


# ---------- 页面 ----------

@bp.route("/speedtest")
def page():
    return render_template("speedtest.html")


# ---------- 接口 ----------

@bp.route("/api/speedtest/ping")
def ping():
    ip = _ip()
    if not _ping_allowed(ip):
        return _json_error(429, "探测过于频繁，请稍后再试")
    resp = jsonify({"ok": True})
    resp.headers["Cache-Control"] = "no-store"
    return resp


@bp.route("/api/speedtest/download")
def download():
    global _concurrent
    ip = _ip()
    try:
        size = int(request.args.get("size", 1024 * 1024))
    except ValueError:
        size = 1024 * 1024
    size = max(DL_MIN, min(DL_REQ_MAX, size))
    if _concurrency_full():
        return _json_error(503, "当前测速人数较多，请稍后再试")
    if not _budget_take(ip, 1, size):
        return _json_error(429, "本小时测速流量已用完，请稍后再试")
    with _state_lock:
        _concurrent += 1
    buf = _get_payload()

    # 并发计数随生成器消费结束而释放（客户端中途断开时 GeneratorExit 也会触发）
    def gen():
        global _concurrent
        try:
            remaining = size
            while remaining > 0:
                chunk = buf[:min(READ_CHUNK, remaining)]
                remaining -= len(chunk)
                yield chunk
        finally:
            with _state_lock:
                _concurrent -= 1

    return Response(gen(), mimetype="application/octet-stream",
                    headers={"Content-Length": str(size),
                             "Cache-Control": "no-store"})


@bp.route("/api/speedtest/upload", methods=["POST"])
def upload():
    global _concurrent
    ip = _ip()
    length = request.content_length
    if length is None or length <= 0:
        return _json_error(400, "缺少请求体")
    if length > UL_REQ_MAX:
        return _json_error(413, "单次上传数据过大")
    if not _budget_take(ip, 2, length):
        return _json_error(429, "本小时测速流量已用完，请稍后再试")
    if _concurrency_full():
        return _json_error(503, "当前测速人数较多，请稍后再试")
    with _state_lock:
        _concurrent += 1
    received = 0
    try:
        stream = request.stream
        remaining = length
        while remaining > 0:
            chunk = stream.read(min(READ_CHUNK, remaining))
            if not chunk:
                break
            received += len(chunk)
            remaining -= len(chunk)
    finally:
        with _state_lock:
            _concurrent -= 1
    resp = jsonify({"ok": True, "bytes": received})
    resp.headers["Cache-Control"] = "no-store"
    return resp
