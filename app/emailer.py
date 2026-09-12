"""邮件发送 — 基于 stdlib smtplib 的最小实现，配置全部来自环境变量。
未配置 MAIL_HOST 时 send_mail 返回 False，调用方给出明确提示；绝不抛出敏感信息。
"""
import logging
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from flask import current_app

log = logging.getLogger("1914.mail")


def mail_configured() -> bool:
    return bool(current_app.config.get("MAIL_HOST"))


def send_mail(to: str, subject: str, html_body: str) -> bool:
    """发送 HTML 邮件。失败返回 False（失败原因记入日志，不暴露给前端）。"""
    cfg = current_app.config
    if not cfg.get("MAIL_HOST"):
        log.warning("MAIL_HOST 未配置，无法发送邮件")
        return False
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = cfg.get("MAIL_FROM") or cfg["MAIL_USER"]
    msg["To"] = to
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    try:
        if cfg.get("MAIL_USE_SSL"):
            # 465 端口（隐式 SSL）
            server = smtplib.SMTP_SSL(cfg["MAIL_HOST"], cfg["MAIL_PORT"], timeout=15,
                                      context=ssl_context())
        else:
            server = smtplib.SMTP(cfg["MAIL_HOST"], cfg["MAIL_PORT"], timeout=15)
        with server:
            server.ehlo()
            if cfg.get("MAIL_USE_TLS") and not cfg.get("MAIL_USE_SSL"):
                server.starttls(context=ssl_context())
                server.ehlo()
            if cfg.get("MAIL_USER"):
                server.login(cfg["MAIL_USER"], cfg["MAIL_PASSWORD"])
            server.send_message(msg)
        return True
    except Exception:
        log.exception("邮件发送失败: to=%s subject=%s", to, subject)
        return False


def ssl_context():
    import ssl
    return ssl.create_default_context()
