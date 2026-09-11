# 1914.fun — 开发启动（Flask 内置服务器 + 调试模式）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
Write-Host "==> 开发服务器 http://127.0.0.1:8000 (Ctrl+C 停止)" -ForegroundColor Green
.\.venv\Scripts\python.exe run.py
