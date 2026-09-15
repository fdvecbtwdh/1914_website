"""生产服务器入口（waitress，Windows 友好）：
  python wsgi.py            默认 0.0.0.0:8000
  环境变量: PORT / HOST 可覆盖

trusted_proxy：cloudflared 在本机连接 waitress，其转发的
X-Forwarded-Proto / X-Forwarded-For 视为可信并应用到请求
（wsgi.url_scheme 正确反映访客真实协议）。没有这一项，
waitress 的 clear_untrusted_proxy_headers 默认会剥离这些头，
后端永远看到 http——FORCE_HTTPS 钩子因此会对所有 https
访客无限 308。
"""
import os

from waitress import serve

from app import create_app

app = create_app()

if __name__ == "__main__":
    host = os.environ.get("HOST", "0.0.0.0")
    port = int(os.environ.get("PORT", "8000"))
    print(f"* 1914.fun 生产服务启动: http://{host}:{port}")
    serve(app, host=host, port=port, threads=8, connection_limit=200,
          channel_timeout=30, ident="1914.fun",
          trusted_proxy="127.0.0.1",
          trusted_proxy_headers=("x-forwarded-proto", "x-forwarded-for"))
