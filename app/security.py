"""IP 封禁与安全事件 — 站点防护的最小实现。
- IP 封禁存 ip_bans 表（到期由查询时间判断，自动解除，无定时任务）；
- 安全事件存 security_events 表（保留 7 天，写入时低概率顺带清理）；
- 真实 IP：Cloudflare Tunnel 部署下 remote_addr 恒为 127.0.0.1，
  读取 Cloudflare 覆写的 CF-Connecting-IP（可用 TRUST_CF_HEADER=0 关闭）。
"""
import random
import re
from datetime import datetime, timedelta

from flask import current_app, has_app_context, request

from . import db

IP_RE = re.compile(r"^[0-9a-fA-F:.]{3,45}$")
EVENT_RETENTION_DAYS = 7


def client_ip() -> str:
    """真实客户端 IP。Cloudflare 会覆写 CF-Connecting-IP，攻击者无法伪造；
    直连（本机/内网调试）时回退 remote_addr。统一小写存储比较。"""
    trusted = True
    if has_app_context():
        trusted = bool(current_app.config.get("TRUST_CF_HEADER", True))
    if trusted:
        cf = (request.headers.get("CF-Connecting-IP") or "").strip().lower()
        if cf and IP_RE.match(cf):
            return cf
    return (request.remote_addr or "?").strip().lower()


def remaining_text(seconds: int) -> str:
    if seconds < 60:
        return "不到 1 分钟"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes} 分钟"
    hours = minutes // 60
    if hours < 48:
        return f"{hours} 小时 {minutes % 60} 分钟"
    days = seconds // 86400
    return f"{days} 天 {hours % 24} 小时"


def active_ban(ip: str) -> dict | None:
    """该 IP 当前是否被封禁（到期即视为解除并顺带清理旧行）。"""
    row = db.query("SELECT * FROM ip_bans WHERE ip = ?", (ip,), one=True)
    if row is None:
        return None
    if row["expires_at"] is not None:
        try:
            exp = datetime.strptime(row["expires_at"][:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            db.execute("DELETE FROM ip_bans WHERE ip = ?", (ip,))
            return None
        if datetime.utcnow() >= exp:
            db.execute("DELETE FROM ip_bans WHERE ip = ?", (ip,))
            return None
        left = int((exp - datetime.utcnow()).total_seconds())
        return {"ip": ip, "reason": row["reason"], "auto": bool(row["auto"]),
                "created_at": row["created_at"], "expires_at": row["expires_at"],
                "permanent": False, "remaining_text": remaining_text(left)}
    return {"ip": ip, "reason": row["reason"], "auto": bool(row["auto"]),
            "created_at": row["created_at"], "expires_at": None,
            "permanent": True, "remaining_text": None}


def ban_ip(ip: str, reason: str, hours: int | None = None,
           auto: bool = True, created_by: int | None = None) -> None:
    """封禁 IP。hours=None 为永久；已存在的封禁被覆盖。"""
    ip = (ip or "").strip().lower()
    if not ip or not IP_RE.match(ip):
        return
    if hours is None:
        expires = None
    else:
        expires = (datetime.utcnow() + timedelta(hours=max(1, hours))
                   ).strftime("%Y-%m-%d %H:%M:%S")
    db.execute(
        """INSERT INTO ip_bans (ip, reason, auto, created_by, expires_at)
           VALUES (?,?,?,?,?)
           ON CONFLICT(ip) DO UPDATE SET reason=excluded.reason,
             auto=excluded.auto, created_by=excluded.created_by,
             created_at=excluded.created_at, expires_at=excluded.expires_at""",
        (ip, reason, 1 if auto else 0, created_by, expires))
    record_event(ip, "ip_ban", "blocked" if auto else "info",
                 f"{'自动防护' if auto else '管理员手动'}封禁（{reason}）")


def unban_ip(ip: str, actor: int | None = None) -> bool:
    ip = (ip or "").strip().lower()
    row = db.query("SELECT 1 FROM ip_bans WHERE ip = ?", (ip,), one=True)
    if row is None:
        return False
    db.execute("DELETE FROM ip_bans WHERE ip = ?", (ip,))
    record_event(ip, "ip_unban", "info", "管理员手动解禁", actor=actor)
    return True


def record_event(ip: str, kind: str, level: str, detail: str = "",
                 actor: int | None = None) -> None:
    """记录一条安全事件（info / suspicious / blocked），保留 7 天。"""
    db.execute(
        """INSERT INTO security_events (ip, kind, level, detail, created_by)
           VALUES (?,?,?,?,?)""",
        ((ip or "")[:45], kind[:40], level[:12], (detail or "")[:300], actor))
    # 低成本清理：约 2% 概率顺带清理过期事件，防止无限膨胀
    if random.random() < 0.02:
        db.execute("DELETE FROM security_events WHERE created_at < "
                   "datetime('now', ?)", (f"-{EVENT_RETENTION_DAYS} days",))
