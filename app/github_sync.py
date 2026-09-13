"""GitHub Issue 同步 — 站点数据库是唯一数据源，同步走队列 + 后台线程。
GITHUB_TOKEN / GITHUB_REPO 未配置、网络失败、Token 失效时：
队列任务安全失败并保留待重试，站点一切功能照常运行。
"""
import json
import threading
import time
import logging
import urllib.request
import urllib.error

from flask import current_app

from . import db

log = logging.getLogger("1914.github_sync")

API = "https://api.github.com"
MAX_ATTEMPTS = 5
POLL_INTERVAL = 15  # 秒


def enqueue(issue_id: int, action: str) -> None:
    """把同步动作放入队列；GitHub 未配置时直接跳过，不入队。
    目标仓库按 Issue 所属分流：web → GITHUB_WEB_REPO，game → GITHUB_REPO。
    """
    try:
        from flask import current_app
        if not current_app.config.get("GITHUB_TOKEN"):
            return  # 未配置 → 完全静默跳过，零依赖
        comp = db.query("SELECT component FROM issues WHERE id = ?",
                        (issue_id,), one=True)
        repo = (current_app.config["GITHUB_WEB_REPO"]
                if comp and comp["component"] == "web"
                else current_app.config["GITHUB_REPO"])
        db.execute(
            "INSERT INTO sync_queue (issue_id, action, repo) VALUES (?,?,?)",
            (issue_id, action, repo))
    except Exception:  # 同步永远不能影响主流程
        log.exception("enqueue failed")


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": f"{current_app.config['SITE_DOMAIN']}-site",
    }


def _gh_request(method: str, url: str, token: str, payload: dict | None = None,
                proxy: str = "") -> dict:
    """urllib 版 GitHub API 调用（避免额外依赖）。4xx/5xx 抛 RuntimeError。
    proxy 形如 http://127.0.0.1:7890（Clash 等本地代理）；空 = 直连。
    """
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(url, data=data, headers=_headers(token), method=method)
    if proxy:
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy, "https": proxy}))
        open_fn = opener.open
    else:
        open_fn = urllib.request.urlopen
    try:
        with open_fn(req, timeout=20) as resp:
            body = resp.read().decode("utf-8")
            return json.loads(body) if body else {}
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"GitHub API {e.code}: {e.read().decode('utf-8', 'replace')[:200]}")


def _issue_payload(issue) -> dict:
    labels = db.query(
        """SELECT l.name FROM content_labels cl JOIN labels l ON l.id = cl.label_id
           WHERE cl.content_type = 'issue' AND cl.content_id = ?""", (issue["id"],))
    body_parts = [issue["body"] or ""]
    body_parts.append(f"\n---\n*来源：[1914.fun]({current_app.config['SITE_URL']}"
                      f"/issue/{issue['id']}) · 作者：{issue['author_name'] or '匿名'}*")
    payload = {
        "title": f"[{current_app.config['SITE_DOMAIN']} #{issue['id']}] {issue['title']}",
        "body": "\n".join(body_parts)[:60000],
    }
    if labels:
        payload["labels"] = [l["name"] for l in labels]
    return payload


def process_queue(app) -> None:
    with app.app_context():
        token = app.config["GITHUB_TOKEN"]
        proxy = app.config.get("GITHUB_PROXY", "")
        if not token:
            return
        tasks = db.query(
            "SELECT * FROM sync_queue WHERE status = 'pending' AND attempts < ? "
            "ORDER BY id ASC LIMIT 10", (MAX_ATTEMPTS,))
        for task in tasks:
            # 目标仓库：任务行未记录（旧数据）→ 默认游戏仓库
            repo = task["repo"] or app.config["GITHUB_REPO"]
            issue = db.query(
                """SELECT i.*, u.username AS author_name FROM issues i
                   LEFT JOIN users u ON u.id = i.author_id WHERE i.id = ?""",
                (task["issue_id"],), one=True)
            if issue is None:
                db.execute("UPDATE sync_queue SET status='skipped', finished_at=datetime('now') "
                           "WHERE id = ?", (task["id"],))
                continue
            try:
                gh_number = issue["github_number"]
                task_repo = task["repo"] or repo
                if task["action"] == "create":
                    data = _gh_request("POST", f"{API}/repos/{task_repo}/issues", token,
                                           _issue_payload(issue), proxy=proxy)
                    db.execute(
                        "UPDATE issues SET github_number = ?, github_url = ?, "
                        "github_synced_at = datetime('now') WHERE id = ?",
                        (data.get("number"), data.get("html_url"), issue["id"]))
                else:
                    if not gh_number:
                        # 尚未在 GitHub 创建 → 先补创建
                        data = _gh_request("POST", f"{API}/repos/{task_repo}/issues", token,
                                           _issue_payload(issue), proxy=proxy)
                        db.execute(
                            "UPDATE issues SET github_number = ?, github_url = ?, "
                            "github_synced_at = datetime('now') WHERE id = ?",
                            (data.get("number"), data.get("html_url"), issue["id"]))
                        gh_number = data.get("number")
                    elif task["action"] == "update":
                        _gh_request("PATCH", f"{API}/repos/{task_repo}/issues/{gh_number}", token,
                                    _issue_payload(issue), proxy=proxy)
                    elif task["action"] in ("close", "reopen"):
                        _gh_request("PATCH", f"{API}/repos/{task_repo}/issues/{gh_number}", token,
                                    {"state": "closed" if task["action"] == "close" else "open"}, proxy=proxy)
                db.execute("UPDATE sync_queue SET status='done', finished_at=datetime('now') "
                           "WHERE id = ?", (task["id"],))
            except Exception as e:
                attempts = task["attempts"] + 1
                status = "failed" if attempts >= MAX_ATTEMPTS else "pending"
                db.execute(
                    "UPDATE sync_queue SET attempts = ?, status = ?, last_error = ? WHERE id = ?",
                    (attempts, status, str(e)[:400], task["id"]))
                log.warning("sync task %s failed (%s/%s): %s",
                            task["id"], attempts, MAX_ATTEMPTS, e)


def _poller_loop(app) -> None:
    while True:
        try:
            process_queue(app)
        except Exception:
            log.exception("github sync poll crashed")
        time.sleep(max(10, app.config["GITHUB_SYNC_INTERVAL"]))


def start_poller(app) -> None:
    """启动后台同步线程（daemon，不阻塞主服务；进程退出自动结束）。"""
    def run():
        time.sleep(5)  # 等服务先起来
        _poller_loop(app)
    t = threading.Thread(target=run, name="github-sync", daemon=True)
    t.start()
