# 1914.fun 一键部署脚本（Windows Server 2022 / 全新机器）
# 用法（管理员 PowerShell，项目目录任意位置）：
#   powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
# 可选参数：
#   -CloudflaredToken <token>   直接配置仪表盘托管隧道（不传则交互询问，可跳过）
#   -SkipPythonCheck            跳过 Python 检测/安装
#   -SkipService                不注册 NSSM 系统服务
#   -SkipTunnel                 不配置 cloudflared
#   -AdminUser x -AdminPassword y   非交互创建管理员
# 脚本幂等：重复运行安全（已有 .env 不覆盖；服务已存在则更新配置）。
param(
    [string]$CloudflaredToken = "",
    [switch]$SkipPythonCheck,
    [switch]$SkipService,
    [switch]$SkipTunnel,
    [string]$AdminUser = "",
    [string]$AdminPassword = ""
)
$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$py = Join-Path $root ".venv\Scripts\python.exe"

function Info($m) { Write-Host "[*] $m" -ForegroundColor Yellow }
function Ok($m)   { Write-Host "[ok] $m" -ForegroundColor Green }
function Fail($m) { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

# ---------- 0. 管理员权限 ----------
$isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
if (-not $isAdmin) { Fail "请以管理员身份运行 PowerShell（安装服务需要）" }

# ---------- 1. Python ----------
if (-not $SkipPythonCheck) {
    if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
        Info "未检测到 Python，自动安装 Python 3.12 ..."
        $inst = "$env:TEMP\python-setup.exe"
        Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile $inst
        Start-Process -FilePath $inst -ArgumentList "/quiet InstallAllUsers=1 PrependPath=1 Include_test=0" -Wait
        $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" +
                    [Environment]::GetEnvironmentVariable("Path", "User")
        if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
            Fail "Python 安装后不可用，请重开 PowerShell 再运行本脚本"
        }
    }
    Ok "Python: $(python --version)"
}

# ---------- 2. 虚拟环境 + 依赖 ----------
if (-not (Test-Path $py)) {
    Info "创建虚拟环境 .venv ..."
    python -m venv .venv
}
Info "安装依赖（官方源失败自动切清华镜像）..."
& $py -m pip install --upgrade pip --quiet
& $py -m pip install -r requirements.txt --index-url https://pypi.org/simple --quiet
if ($LASTEXITCODE -ne 0) {
    Info "官方源失败，尝试清华镜像 ..."
    & $py -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple --quiet
    if ($LASTEXITCODE -ne 0) { Fail "依赖安装失败，请检查网络" }
}
Ok "依赖安装完成"

# ---------- 3. .env ----------
$envFile = Join-Path $root ".env"
if (-not (Test-Path $envFile)) {
    Info "生成 .env（随机 SESSION_SECRET，生产默认值）"
    $secret = -join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
    $content = [IO.File]::ReadAllText((Join-Path $root ".env.example"), [Text.Encoding]::UTF8)
    $content = $content -replace 'change-me-to-a-long-random-string', $secret
    # 路径跟随部署目录（.env 里用正斜杠）
    $rootUrl = $root -replace '\\', '/'
    $content = $content -replace [regex]::Escape('D:/Code/1914_website'), $rootUrl
    # 服务器上没有游戏项目：清空路径 → 自动使用内置 seed/ 卡牌副本
    $content = $content -replace 'GAME_PROJECT_PATH=.*', 'GAME_PROJECT_PATH='
    [IO.File]::WriteAllText($envFile, $content, [Text.Encoding]::UTF8)
    Ok ".env 已生成"
} else {
    Ok ".env 已存在，保留现有配置不覆盖"
}
# 确保关键行存在（补齐缺失项，不改已有值）
function Ensure-EnvLine($key, $value) {
    $lines = Get-Content $envFile
    if (-not ($lines | Where-Object { $_ -match "^$key=" })) {
        Add-Content -Path $envFile -Value "$key=$value"
    }
}
Ensure-EnvLine "HOST" "127.0.0.1"
Ensure-EnvLine "PORT" "8000"

# ---------- 4. 数据库 + 官方卡牌 + 管理员 ----------
Info "初始化数据库 ..."
& $py manage.py init-db
Info "导入官方卡牌 ..."
& $py manage.py import-cards
& $py manage.py create-labels
Info "创建管理员 ..."
if ($AdminUser -and $AdminPassword) {
    & $py manage.py create-admin --username $AdminUser --password $AdminPassword
} else {
    & $py manage.py create-admin
}

# ---------- 5. NSSM 系统服务 ----------
if (-not $SkipService) {
    $nssm = "C:\Windows\System32\nssm.exe"
    if (-not (Test-Path $nssm)) {
        Info "下载 NSSM ..."
        $zip = "$env:TEMP\nssm.zip"
        Invoke-WebRequest "https://nssm.cc/release/nssm-2.24.zip" -OutFile $zip
        Expand-Archive $zip "$env:TEMP\nssm" -Force
        Copy-Item "$env:TEMP\nssm\nssm-2.24\win64\nssm.exe" $nssm -Force
    }
    $svc = "1914site"
    # 停掉可能存在的手动进程，避免端口冲突
    Get-CimInstance Win32_Process -Filter "Name = 'python.exe'" |
        Where-Object { $_.CommandLine -like "*wsgi.py*" } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
    if (Get-Service $svc -ErrorAction SilentlyContinue) {
        Info "服务 $svc 已存在，重建以更新配置 ..."
        nssm stop $svc 2>$null | Out-Null
        nssm remove $svc confirm | Out-Null
    }
    nssm install $svc "$py" "wsgi.py" | Out-Null
    nssm set $svc AppDirectory $root
    nssm set $svc AppEnvironmentExtra HOST=127.0.0.1 PORT=8000 PYTHONOPTIMIZE=1
    nssm set $svc AppStdout (Join-Path $root "data\service.log")
    nssm set $svc AppStderr (Join-Path $root "data\service.log")
    nssm set $svc AppRotateFiles 1
    nssm set $svc Start SERVICE_AUTO_START
    nssm start $svc
    Ok "服务 $svc 已启动（开机自启，日志 data\service.log）"
}

# ---------- 6. cloudflared 隧道 ----------
if (-not $SkipTunnel) {
    if (-not $CloudflaredToken) {
        $CloudflaredToken = Read-Host "粘贴 Cloudflare Tunnel token（留空 = 跳过隧道配置）"
    }
    if ($CloudflaredToken.Trim()) {
        $cf = "C:\Windows\System32\cloudflared.exe"
        if (-not (Test-Path $cf)) {
            Info "下载 cloudflared ..."
            Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile $cf
        }
        if (Get-Service cloudflared -ErrorAction SilentlyContinue) {
            Info "cloudflared 服务已存在，跳过安装"
        } else {
            & $cf service install $CloudflaredToken.Trim()
        }
        Ok "cloudflared 已配置 —— 记得到 Zero Trust 仪表盘添加 Public Hostname："
        Write-Host "    1914.fun -> HTTP -> localhost:8000" -ForegroundColor Cyan
    } else {
        Info "跳过隧道配置（之后参照 DEPLOY.md 第 9 节手动配置）"
    }
}

# ---------- 7. 验证 ----------
$startedByScript = $false
if (-not $SkipService) {
    Start-Sleep -Seconds 3
    $startedByScript = $true
}
if ($startedByScript) {
    try {
        $r = Invoke-WebRequest "http://127.0.0.1:8000/" -TimeoutSec 8 -UseBasicParsing
        Ok "本机验证通过：HTTP $($r.StatusCode)"
    } catch {
        Fail "本机验证失败，请查看日志: Get-Content $root\data\service.log -Tail 30"
    }
}

# ---------- 完成 ----------
Write-Host ""
Ok "部署完成！"
Write-Host "  - 服务:     1914site（nssm start/stop/restart 1914site）"
Write-Host "  - 日志:     data\service.log"
Write-Host "  - 手动启停: 双击 start.bat / stop.bat"
if (-not $SkipTunnel -and -not $CloudflaredToken) {
    Write-Host "  - 隧道:     未配置，参照 DEPLOY.md 第 9 节"
}
Write-Host "  - 上线后记得: 管理员登录 /settings 修改初始密码"
