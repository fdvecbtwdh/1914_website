# 1914.fun 代码同步脚本：本地改 → 打包上传 → 重启服务 → 健康检查
#
# 用法（在项目目录或任意位置，普通 PowerShell 即可，不需要管理员）：
#   powershell -ExecutionPolicy Bypass -File scripts\push.ps1
#
# 参数：
#   -Force       工作区有未提交改动时仍然部署（默认拒绝，防止忘了 commit）
#   -NoRestart   只上传文件，不重启服务（配合手工验证）
#   -SkipChecks  跳过本地 Python 语法检查
#   -DryRun      只打包并列出文件，不上传、不重启
#   -Server / -RemoteUser / -RemoteRoot / -Service / -PublicUrl  覆盖默认目标
#
# 做的事：
#   1. 校验：git 工作区干净、本地 Python 语法能过
#   2. 用 tar 打包（排除 .git/.venv/data/backups/logs/__pycache__/*.db/.env）
#   3. 远程先把 app 与 enigma_site 打成 backups\backup-<时间戳>.tar.gz
#   4. scp 上传 → 远程 bsdtar 解包覆盖 → 删除临时包
#   5. Restart-Service 1914site → 轮询健康检查（先本机 127.0.0.1:8000，再公网）
#
# 回滚：ssh 到服务器，在 E:\#Code\1914_website 下执行
#   tar -xzf backups\backup-<时间戳>.tar.gz
#   然后 Restart-Service 1914site
param(
    [string]$Server = "192.168.71.7",
    [string]$RemoteUser = "Administrator",
    [string]$RemoteRoot = 'E:\#Code\1914_website',
    [string]$Service = "1914site",
    [string]$PublicUrl = "https://1914.fun/",
    [switch]$Force,
    [switch]$NoRestart,
    [switch]$SkipChecks,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
$root = Split-Path $PSScriptRoot -Parent
Set-Location $root
$target = "$RemoteUser@$Server"
$started = Get-Date

function Info($m) { Write-Host "[*] $m" -ForegroundColor Yellow }
function Ok($m)   { Write-Host "[ok] $m" -ForegroundColor Green }
function Warn($m) { Write-Host "[!] $m" -ForegroundColor DarkYellow }
function Fail($m) { Write-Host "[x] $m" -ForegroundColor Red; exit 1 }

# 让中文在 UTF-8 与 GBK 控制台下都不会乱码（PS 5.1 默认按控制台代码页输出）
try {
    chcp 65001 > $null
    [Console]::OutputEncoding = [System.Text.Encoding]::UTF8
} catch { }

# ---------- 1. 本地校验 ----------
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Fail "找不到 git" }
if (-not (Test-Path (Join-Path $root "app"))) { Fail "当前目录不像项目根：$root" }

$branch = (& git rev-parse --abbrev-ref HEAD) 2>$null
$commit = (& git rev-parse --short HEAD) 2>$null
# 这些文件本来就不打包上传（见下面 tar 排除表），不该挡住部署
$dirtyIgnored = @('.zcodeignore')
$dirty = @(& git status --porcelain | Where-Object {
    $line = "$_".Trim()
    if ($line.Length -le 2) { return $false }
    $path = $line.Substring(2).Trim().Trim('"')
    ($dirtyIgnored -notcontains $path) -and ($line -notmatch '^\?\? \.run/')
})
if ($dirty.Count -gt 0 -and -not $Force) {
    $dirty | Select-Object -First 10 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    Fail ("工作区有 {0} 处未提交改动。先 git commit，或加 -Force 强行部署" -f $dirty.Count)
}
if ($dirty.Count -gt 0) { Warn ("工作区有 {0} 处未提交改动（-Force 已放行）" -f $dirty.Count) }
Ok ("仓库 {0} @ {1}" -f $branch, $commit)

if (-not $SkipChecks) {
    if (Get-Command python -ErrorAction SilentlyContinue) {
        & python -m compileall -q app 2>&1 | Out-Null
        if ($LASTEXITCODE -ne 0) { Fail "Python 语法检查失败，先修好再部署" }
        Ok "Python 语法检查通过"
    } else {
        Warn "未找到 python，跳过语法检查（可用 -SkipChecks 显式跳过）"
    }
}

# ---------- 2. 打包 ----------
$ts = Get-Date -Format "yyyyMMdd-HHmmss"
$archive = Join-Path $env:TEMP "1914-deploy-$ts.tar.gz"
$excludes = @(
    "--exclude=.git", "--exclude=.venv", "--exclude=data", "--exclude=backups",
    "--exclude=logs", "--exclude=__pycache__", "--exclude=.pytest_cache",
    "--exclude=*.db", "--exclude=*.db-wal", "--exclude=*.db-shm",
    "--exclude=.env", "--exclude=.zcodeignore"
)
& tar -czf $archive @excludes -C $root .
if ($LASTEXITCODE -ne 0) { Fail "打包失败" }
$files = @(& tar -tzf $archive | Where-Object { $_ -notmatch '/$' })
$sizeMb = (Get-Item $archive).Length / 1MB
Ok ("打包 {0} 个文件 · {1:N1} MB" -f $files.Count, $sizeMb)

if ($DryRun) {
    Info "DryRun：文件清单前 40 条"
    $files | Select-Object -First 40 | ForEach-Object { Write-Host "      $_" -ForegroundColor DarkGray }
    Remove-Item $archive -Force
    Info "DryRun 结束，未上传、未重启"
    return
}

# ---------- 3. 远程检查 + 备份 ----------
& ssh -o BatchMode=yes -o ConnectTimeout=10 $target "echo ok" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "SSH 连不上 $target（检查网络或公钥）" }
Ok "SSH 连通 $target"

$backupName = "backup-$ts.tar.gz"
$remoteBackup = "$RemoteRoot\backups\$backupName"
& ssh -o BatchMode=yes $target "tar -czf $remoteBackup -C $RemoteRoot app enigma_site" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn "远程备份失败（继续部署，但这次没有回滚点）"
} else {
    Ok "远程备份 backups\$backupName"
}

# ---------- 4. 上传 + 解包 ----------
$remoteSlash = $RemoteRoot -replace '\\', '/'
$remoteTmpName = ".deploy-$ts.tar.gz"
$dest = "{0}:{1}/{2}" -f $target, $remoteSlash, $remoteTmpName
& scp -q $archive $dest
if ($LASTEXITCODE -ne 0) { Fail "上传失败：$dest" }
Ok "上传完成"

$remoteTmpPath = "$RemoteRoot\$remoteTmpName"
& ssh -o BatchMode=yes $target "tar -xzf $remoteTmpPath -C $RemoteRoot" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Fail "远程解包失败（临时包保留在 $remoteTmpPath）" }
& ssh -o BatchMode=yes $target "del `"$remoteTmpPath`"" 2>&1 | Out-Null
Ok "解包覆盖完成"
Remove-Item $archive -Force

# ---------- 5. 重启 ----------
if ($NoRestart) {
    Warn "已跳过重启（-NoRestart），改动要等服务重启后才生效"
} else {
    & ssh -o BatchMode=yes $target "powershell -NoProfile -Command Restart-Service $Service" 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { Fail "重启 $Service 失败" }
    Ok "已重启 $Service"

    # ---------- 6. 健康检查 ----------
    $localOk = $false
    $publicOk = $false
    $deadline = (Get-Date).AddSeconds(60)
    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds 2
        if (-not $localOk) {
            $probe = "powershell -NoProfile -Command (Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/ -TimeoutSec 5).StatusCode"
            $code = (& ssh -o BatchMode=yes $target $probe 2>$null)
            if ("$code".Trim() -eq "200") { $localOk = $true; Ok "源站 127.0.0.1:8000 返回 200" }
        }
        if ($localOk -and -not $publicOk) {
            try {
                $resp = Invoke-WebRequest -UseBasicParsing $PublicUrl -TimeoutSec 10
                if ($resp.StatusCode -eq 200) { $publicOk = $true; Ok "公网 $PublicUrl 返回 200" }
            } catch { }
        }
        if ($localOk -and $publicOk) { break }
    }
    if (-not $localOk)  { Fail "源站 60 秒内没返回 200，看 E:\#Code\1914_website\logs 或服务状态" }
    if (-not $publicOk) { Warn "源站正常，但公网地址没拿到 200（可能是 Cloudflare 或 DNS 的问题）" }
}

$elapsed = [int]((Get-Date) - $started).TotalSeconds
Ok ("部署完成 · {0} 个文件 · 用时 {1}s · {2} @ {3}" -f $files.Count, $elapsed, $branch, $commit)
Info "回滚：ssh $target，在 $RemoteRoot 下执行 tar -xzf backups\$backupName，再 Restart-Service $Service"
