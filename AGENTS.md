# AGENTS.md

## Agent skills

### Issue tracker

GitHub Issues in `fdvecbtwdh/1914_website`. See `docs/agents/issue-tracker.md`.

### Triage labels

Default triage roles: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

multi-context. See `docs/agents/domain.md`.

## Remote server (SSH)

From the dev machine's PowerShell just run `ssh Administrator@192.168.71.7` (same LAN `192.168.71.x`; the box is Windows Server 2022, hostname `WIN-8SHRLLD3EML`, default shell `cmd.exe`, so for PowerShell use `ssh Administrator@192.168.71.7 "powershell -NoProfile -Command <cmd>"`); key auth is already set up — the dev machine's `%USERPROFILE%\.ssh\id_ed25519.pub` is in the server's `C:\ProgramData\ssh\administrators_authorized_keys` (ACL restricted to Administrators + SYSTEM), so `ssh -o BatchMode=yes` works without a password; the live site is `E:\#Code\1914_website` (`.venv` = Python deps, `.env` = config, `data\service.log` = service log), served by NSSM service `1914site` running waitress on `127.0.0.1:8000` and exposed through the `Cloudflared` service to `https://1914.fun`, so a code change only takes effect after `powershell -NoProfile -Command Restart-Service 1914site`; never edit files on the server directly — change the local repo and run `powershell -ExecutionPolicy Bypass -File scripts\push.ps1` (it refuses a dirty worktree, backs the remote up to `backups\backup-<ts>.tar.gz`, uploads with `scp`+`tar`, restarts and health-checks), and when you need to read remote files `scp` them down and grep locally instead of nesting quotes inside ssh (multi-layer quoting silently returns empty output with exit 255); the server already has `tar` (bsdtar 3.5.2) and the OpenSSH client, so no extra tooling is needed.
