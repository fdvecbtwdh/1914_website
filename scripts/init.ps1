# 1914.fun 站点 — 初始化数据库 + 导入官方卡牌 + 创建管理员（PowerShell）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\init.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Test-Path ".env")) {
    Write-Host "==> 首次运行：从 .env.example 生成 .env" -ForegroundColor Yellow
    Copy-Item .env.example .env
    $secret = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
    (Get-Content .env) -replace 'change-me-to-a-long-random-string', $secret |
        Set-Content .env -Encoding UTF8
    Write-Host "    已生成随机 SESSION_SECRET。请按需编辑 .env（数据库路径 / GitHub 同步等）" -ForegroundColor Cyan
}

Write-Host "==> 初始化数据库 ..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe manage.py init-db

Write-Host "==> 导入官方卡牌（来自游戏项目 data/cards）..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe manage.py import-cards

Write-Host "==> 创建初始标签 ..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe manage.py create-labels

Write-Host "==> 创建管理员账号 ..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe manage.py create-admin

Write-Host "==> 初始化完成！开发启动: scripts\start-dev.ps1 ；生产启动: scripts\start-prod.ps1" -ForegroundColor Green
