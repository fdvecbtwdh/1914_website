"""AZ 背单词（/az）反代：把 /az 与 /az/... 原样转发给本机 Node 词典服务。

为什么用反代而不是让 cloudflared 多配一条 ingress：
本机的 cloudflared 是 token 托管的隧道，ingress 规则在 Cloudflare 面板里，
改面板要人工操作；而 1914.fun 的所有流量本来就已经进了 Flask，
在这里按路径分流最省事，也不需要改动隧道配置。

上游（E:\\#Code\\az-vocab\\server\\index.mjs，Windows 服务 azvocab）：
  /az/api/*   只读词典接口
  /az/*       单页前端静态资源（SPA fallback）

约束：
- 只读转发，不写本站任何状态；
- 上游挂了就返回 503 提示页，绝不影响本站其他路由；
- 路径不改写：上游自己也以 /az 为前缀；
- 本模块只在导航页 /nav 暴露入口，不改动其他页面。
"""
import http.client
import logging

from flask import Blueprint, Response, redirect, request, stream_with_context

bp = Blueprint("az", __name__)

UPSTREAM_HOST = "127.0.0.1"
UPSTREAM_PORT = 8010
TIMEOUT_SEC = 60
CHUNK = 64 * 1024

# 逐跳头不能转发（RFC 7230 6.1）
HOP_BY_HOP = frozenset({
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host",
    "content-length",
})

OFFLINE_HTML = """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>AZ 背单词 · 暂时不可用</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
body{font-family:system-ui,-apple-system,"Segoe UI",sans-serif;max-width:36rem;
     margin:14vh auto;padding:0 1.4rem;color:#2b2b2b;line-height:1.75}
h1{font-size:1.25rem;margin:0 0 .8rem}
p{color:#5d5d5d;margin:.5rem 0}
code{background:#f2f3f5;padding:.1rem .35rem;border-radius:4px;font-size:.86em}
a{color:#2563eb}
</style></head><body>
<h1>AZ 背单词暂时不可用</h1>
<p>词典服务（127.0.0.1:8010）没有响应，站点其他功能不受影响。</p>
<p>排查：<code>Get-Service azvocab</code>，或查看 <code>E:\\#Code\\az-vocab\\service.err.log</code>。</p>
<p><a href="/nav">返回导航</a></p>
</body></html>"""


def _oh_no(reason: str) -> Response:
    logging.getLogger(__name__).warning("AZ 上游不可用: %s", reason)
    resp = Response(OFFLINE_HTML, status=503, mimetype="text/html")
    resp.headers["Retry-After"] = "30"
    resp.headers["Cache-Control"] = "no-store"
    return resp


def _forward() -> Response:
    target = request.full_path if request.query_string else request.path

    headers = {k: v for k, v in request.headers.items()
               if k.lower() not in HOP_BY_HOP}
    body = None
    if request.method in ("POST", "PUT", "PATCH", "DELETE"):
        body = request.get_data()

    conn = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=TIMEOUT_SEC)
    try:
        conn.request(request.method, target, body=body, headers=headers)
        upstream = conn.getresponse()
    except (OSError, http.client.HTTPException) as exc:
        conn.close()
        return _oh_no(f"{type(exc).__name__}: {exc}")

    out_headers = [(k, v) for k, v in upstream.getheaders()
                   if k.lower() not in HOP_BY_HOP]

    if request.method == "HEAD":
        conn.close()
        return Response(b"", status=upstream.status, headers=out_headers)

    def generate():
        try:
            while True:
                chunk = upstream.read(CHUNK)
                if not chunk:
                    break
                yield chunk
        finally:
            try:
                upstream.close()
                conn.close()
            except Exception:  # noqa: BLE001 - 关闭失败不影响已发响应
                pass

    return Response(stream_with_context(generate()), status=upstream.status,
                    headers=out_headers)


# 上游 /az/api/words/batch 是 POST：Flask 的 route 默认只允许 GET，
# 不显式放开方法时浏览器拿到 405，前端按 id 水合词条会整片失败。
AZ_METHODS = ["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]


@bp.route("/az", methods=AZ_METHODS)
def az_bare():
    """/az 少了结尾斜杠，重定向到 /az/，否则前端相对路径会算错。"""
    return redirect("/az/", code=302)


@bp.route("/az/", defaults={"subpath": ""}, methods=AZ_METHODS)
@bp.route("/az/<path:subpath>", methods=AZ_METHODS)
def az_entry(subpath: str):  # noqa: ARG001 - 路径本身由 request 取，subpath 只为注册规则
    return _forward()
