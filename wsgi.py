"""生产服务器入口（waitress，Windows 友好）：
  python wsgi.py            默认 0.0.0.0:8000
  环境变量: PORT / HOST 可覆盖
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
          channel_timeout=30, ident="1914.fun")
