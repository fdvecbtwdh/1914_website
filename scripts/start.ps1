# 1914.fun 一键启动（后台运行）
# 用法: scripts\start.ps1 [-OpenBrowser]  ；start.bat 已封装好双击使用
# 行为: 已在运行则直接提示；否则后台拉起 waitress（隐藏窗口，日志写 data\server.log），
#       轮询健康检查直到成功，失败时打印日志末尾。
param([switch]$OpenBrowser)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root

$py = Join-Path $root ".venv\Scripts\python.exe"
if (-not (Test-Path $py)) {
    Write-Host "[x] 未找到虚拟环境，请先运行 scripts\install.ps1" -ForegroundColor Red
    exit 1
}

$port = if ($env:PORT) { $env:PORT } else { "8000" }
$base = "http://127.0.0.1:$port"

function Test-Site {
    try {
        $r = Invoke-WebRequest "$base/" -TimeoutSec 2 -UseBasicParsing
        return ($r.StatusCode -eq 200)
    } catch { return $false }
}

if (Test-Site) {
    Write-Host "[ok] 服务器已在运行: $base" -ForegroundColor Green
    if ($OpenBrowser) { Start-Process $base }
    exit 0
}

# 依赖快速检查
& $py -c "import waitress, flask, argon2" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host "[x] 依赖未安装或数据库未初始化，请先运行 scripts\install.ps1 和 scripts\init.ps1" -ForegroundColor Red
    exit 1
}

if (-not (Test-Path (Join-Path $root "data"))) {
    New-Item -ItemType Directory -Path (Join-Path $root "data") | Out-Null
}

Write-Host "[..] 正在后台启动 1914.fun ..." -ForegroundColor Yellow
$out = Join-Path $root "data\server.log"
$err = Join-Path $root "data\server.err.log"
Start-Process -FilePath $py -ArgumentList "wsgi.py" -WorkingDirectory $root `
    -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err

$ok = $false
for ($i = 0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    if (Test-Site) { $ok = $true; break }
}

if ($ok) {
    Write-Host "[ok] 启动成功: $base （日志: data\server.log）" -ForegroundColor Green
    if ($OpenBrowser) { Start-Process $base }
    exit 0
} else {
    Write-Host "[x] 启动失败，日志末尾:" -ForegroundColor Red
    if (Test-Path $err) { Get-Content $err -Tail 15 }
    if (Test-Path $out) { Get-Content $out -Tail 15 }
    exit 1
}
