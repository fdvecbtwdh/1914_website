# 1914.fun 站点 — 安装依赖（PowerShell）
# 用法: powershell -ExecutionPolicy Bypass -File scripts\install.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

Write-Host "==> 创建 Python 虚拟环境 .venv ..." -ForegroundColor Yellow
if (-not (Test-Path ".venv\Scripts\python.exe")) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw "python 未找到，请先安装 Python 3.11+ 并加入 PATH" }
}

Write-Host "==> 升级 pip ..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe -m pip install --upgrade pip --quiet

Write-Host "==> 安装依赖 (requirements.txt) ..." -ForegroundColor Yellow
.\.venv\Scripts\python.exe -m pip install -r requirements.txt --index-url https://pypi.org/simple
if ($LASTEXITCODE -ne 0) {
    Write-Host "官方源失败，尝试清华镜像 ..." -ForegroundColor Yellow
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
}

Write-Host "==> 依赖安装完成" -ForegroundColor Green
