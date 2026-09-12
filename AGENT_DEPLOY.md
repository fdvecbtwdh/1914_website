# AGENT_DEPLOY

Machine-readable deploy guide. Target: Windows 10/11/Server 2019+, PowerShell **as Administrator**. Repo root = deploy root. All commands idempotent unless noted.

## Fast path (does steps 2-5, auto: python/venv/deps/.env/db/admin/NSSM service)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
# optional flags: -SkipService -SkipTunnel -SkipPythonCheck -AdminUser <U> -AdminPassword <P> -CloudflaredToken <T>
```

If fast path succeeds (final line "部署完成"), skip to VERIFY.

## Manual fallback

### 1. Python

```powershell
if (-not (Get-Command python)) {
  Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile "$env:TEMP\py.exe"
  & "$env:TEMP\py.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0
  # open NEW shell afterwards; check: python --version
}
```

### 2. deps

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt --index-url https://pypi.org/simple
# fallback index: https://pypi.tuna.tsinghua.edu.cn/simple
```

### 3. .env

Copy `.env.example` → `.env`. Required mutations:
- `SESSION_SECRET` = 64 random hex chars (NOT the placeholder)
- replace literal `D:/Code/1914_website` with `<deploy root, forward slashes>` in DATABASE_URL / UPLOAD_DIR
- `GAME_PROJECT_PATH=` (empty; seeds fallback)
- `COOKIE_SECURE=1` (must be 1 behind HTTPS; set 0 only for plain-http local debug)
- ensure lines exist: `HOST=127.0.0.1`, `PORT=8000`
- optional: `GAME_SERVER_TOKEN` = random string — enables the game-server panel on `/status`
  (game server pushes heartbeats to /api/game-server/heartbeat; empty = panel shows 不可用)

### 4. db + admin

```powershell
.\.venv\Scripts\python manage.py init-db
.\.venv\Scripts\python manage.py import-cards     # expect "新建 16 / 更新 0" (or 更新 16)
.\.venv\Scripts\python manage.py create-labels
.\.venv\Scripts\python manage.py create-admin --username <U> --password <P>
# password rule: >=8 chars, must contain letters AND digits
```

### 5. run + verify

```powershell
.\.venv\Scripts\python wsgi.py          # foreground; Ctrl+C stops
# GET http://127.0.0.1:8000/          → 301 → /index
# GET http://127.0.0.1:8000/index      → 200, contains "1914"
# GET http://127.0.0.1:8000/nav        → 200, hub page
# GET http://127.0.0.1:8000/status     → 200, server status (web panel 在线, game panel 不可用 until heartbeats)
```

### 6. service (production)

```powershell
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -SkipPythonCheck -SkipTunnel -AdminUser <U> -AdminPassword <P>
# installs NSSM service "1914site": auto-start, restart-on-crash
nssm status 1914site      # SERVICE_RUNNING
Get-Content data\service.log -Tail 20
```

### 7. tunnel (optional)

cloudflared (dashboard-managed): create tunnel in Zero Trust → copy token →

```powershell
Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile "C:\Windows\System32\cloudflared.exe"
cloudflared service install <TOKEN>
# dashboard: Public Hostname 1914.fun -> HTTP -> localhost:8000
```

## db reset / recovery

- delete `data914.db` (or whole `data\`) then start → app auto-rebuilds:
  schema + 16 official cards + labels + fresh admin (random password printed
  to console / `data\service.log` as `[1914] ... admin / <password>`)
- trigger: users table empty at startup (`AUTO_SEED=1` in .env, default on)
- existing non-empty DBs are never touched by auto-seed

## tests

```powershell
.\.venv\Scripts\python -m unittest tests.test_site   # expect: OK (54 tests)
```

## update

```powershell
git pull
nssm restart 1914site
# card data changed: seed\cards\*.json then admin button "导入官方卡牌" or:
.\.venv\Scripts\python manage.py import-cards
```

## troubleshoot

- port busy: `netstat -ano | findstr :8000`
- log: `data\service.log` (service) / `data\server.err.log` (manual)
- pip SSL fails → tsinghua index above
- cannot login → COOKIE_SECURE must be 0 for plain http, 1 for https; changing SESSION_SECRET invalidates sessions
- 403 admin → need role=admin user
