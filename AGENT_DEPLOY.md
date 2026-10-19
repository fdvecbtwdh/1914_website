# AGENT_DEPLOY

Machine-readable deploy guide. Target: Windows 10/11/Server 2019+, PowerShell **as Administrator**. Repo root = deploy root. All commands idempotent unless noted.

## Remote access (SSH) — read this first

From your dev machine's PowerShell just run `ssh Administrator@192.168.71.7` (same LAN `192.168.71.x`; the box is Windows Server 2022, hostname `WIN-8SHRLLD3EML`, default shell `cmd.exe`, so for PowerShell use `ssh Administrator@192.168.71.7 "powershell -NoProfile -Command <cmd>"`); key auth is already set up — your `%USERPROFILE%\.ssh\id_ed25519.pub` is in the server's `C:\ProgramData\ssh\administrators_authorized_keys` (ACL restricted to Administrators + SYSTEM), so `ssh -o BatchMode=yes` works without a password and you should not need password login. The live site is `E:\#Code\1914_website` (`.venv` holds Python deps, `.env` holds config, `data\service.log` is the service log), served by NSSM service `1914site` running waitress on `127.0.0.1:8000` and exposed through the `Cloudflared` service to `https://1914.fun`, so any code change only takes effect after `powershell -NoProfile -Command Restart-Service 1914site`; never edit files on the server directly — make changes in the local repo and run `powershell -ExecutionPolicy Bypass -File scripts\push.ps1` (it refuses a dirty worktree, backs the remote up to `backups\backup-<ts>.tar.gz`, uploads via `scp`+`tar`, restarts, then health-checks), and when you need to read remote files `scp` them down and grep locally instead of nesting quotes inside ssh (multi-layer quoting silently returns empty output with exit 255); the server already has `tar` (bsdtar 3.5.2) and the OpenSSH client, so no extra tooling is required.

## Fast path (does steps 2-5, auto: python/venv/deps/.env/db/admin/NSSM service)

```powershell
Set-ExecutionPolicy -Scope Process Bypass
powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
# optional flags: -SkipService -SkipTunnel -SkipPythonCheck -AdminUser <U> -AdminPassword <P> -CloudflaredToken <T>
```

If fast path succeeds (final line "部署完成"), skip to VERIFY.

## Redeploy after the first install (run on the DEV machine, not the server)

Never edit files on the server: `scripts\push.ps1` overwrites them from the local repo.

```powershell
cd <repo>
powershell -ExecutionPolicy Bypass -File scripts\push.ps1
```

What it does: refuses if the git worktree has uncommitted changes (`-Force` overrides) -> python bytecode check -> `tar` the worktree excluding `.git/.venv/data/backups/logs/__pycache__/*.db/.env` -> remote backup to `backups\backup-<ts>.tar.gz` -> `scp` + remote `tar -xzf` -> `Restart-Service 1914site` -> poll `http://127.0.0.1:8000/` then the public URL.

Flags: `-Force` (deploy dirty worktree) `-NoRestart` `-SkipChecks` `-DryRun` `-Server/-RemoteUser/-RemoteRoot/-Service/-PublicUrl`.

Rollback on the server: `tar -xzf backups\backup-<ts>.tar.gz` in the deploy root, then `Restart-Service 1914site`.

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
- `FORCE_HTTPS=` (empty = follow COOKIE_SECURE; http requests via the proxy get a
  308 to SITE_URL — required so http-entry users don't lose session/CSRF cookies)
- ensure lines exist: `HOST=127.0.0.1`, `PORT=8000`
- optional: `GITHUB_WEB_REPO` = repo for web-issue sync (default `fdvecbtwdh/1914_website`)
- optional: `GITHUB_PROXY` = local proxy URL (e.g. Clash `http://127.0.0.1:7890`) for GitHub sync when GitHub is unreachable directly
- optional mail block (enables "forgot password" email recovery; unset = disabled):
  `MAIL_HOST` / `MAIL_PORT` (587 STARTTLS default, `MAIL_USE_SSL=1` for 465 SSL) /
  `MAIL_USER` / `MAIL_PASSWORD` / `MAIL_FROM` (must be a verified sender at the provider) /
  `MAIL_USE_TLS=1` / `RECOVER_TOKEN_MINUTES=30`
- optional: `DB_TEMPLATE_PATH` — override reset-template location
  (`manage.py save-default` writes `data\default.db` next to the DB)
- optional: `STATUS_SAMPLER_ENABLED=0` — disable the /status metrics sampler thread
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

- delete `data\1914.db` (or whole `data\`) then start → app auto-rebuilds:
  schema + 16 official cards + labels + fresh admin (random password printed
  to console / `data\service.log` as `[1914] ... admin / <password>`)
- trigger: users table empty at startup (`AUTO_SEED=1` in .env, default on)
- existing non-empty DBs are never touched by auto-seed
- default template: `python manage.py save-default` writes `data\default.db`;
  if the DB file is missing at startup and this template exists → restored from it verbatim
  (keeps accounts, card tags, all data). `AUTO_SEED=0` disables seeding only

## tests

```powershell
.\.venv\Scripts\python -m unittest tests.test_site   # expect: OK (142 tests)
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
