# 1914.fun — 生产启动（waitress，Windows 友好的 WSGI 服务器）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\start-prod.ps1
#       可先 $env:PORT = 8000 指定端口
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
$port = if ($env:PORT) { $env:PORT } else { "8000" }
Write-Host "==> 生产服务器 http://0.0.0.0:$port (供 Cloudflare Tunnel 回源)" -ForegroundColor Green
$env:PYTHONOPTIMIZE = "1"
.\.venv\Scripts\python.exe wsgi.py
