"""1914.fun 站点测试套件 — stdlib unittest + Flask test client。
运行: .venv/Scripts/python -m unittest discover -s tests -v
"""
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import create_app, db  # noqa: E402
from app.auth import hash_password  # noqa: E402


def png_bytes() -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (10, 10), (200, 100, 50)).save(buf, format="PNG")
    return buf.getvalue()


class Base(unittest.TestCase):
    """每个测试方法独立的 app + 独立临时数据库。"""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="1914_case_")
        config = {
            "DB_PATH": os.path.join(self.tmp, "test.db"),
            "UPLOAD_DIR": Path(self.tmp) / "uploads",
            "TESTING": True,
            "SECRET_KEY": "test-secret",
            "GITHUB_SYNC_ENABLED": False,
            "GITHUB_TOKEN": "",
            "COOKIE_SECURE": False,
        }
        self.app = create_app(config)
        self.client = self.app.test_client()
        with self.app.app_context():
            db.init_db(self.app.config["DB_PATH"])

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def set_csrf(self, token="t"):
        """在当前会话中写入 CSRF token（保留登录态）。"""
        with self.client.session_transaction() as s:
            s["csrf"] = token
        return token

    def csrf_hdr(self, token="t"):
        return {"X-CSRF-Token": self.set_csrf(token)}

    def register(self, username="tester", password="Passw0rd123", email=""):
        self.set_csrf()
        r = self.client.post("/register", data={
            "csrf_token": "t",
            "username": username, "password": password,
            "confirm": password, "email": email,
        }, follow_redirects=True)
        self.set_csrf()  # 认证会重置会话，重新固定 CSRF
        return r

    def login(self, ident="tester", password="Passw0rd123"):
        self.set_csrf()
        r = self.client.post("/login", data={
            "csrf_token": "t", "username": ident, "password": password},
            follow_redirects=True)
        self.set_csrf()  # 认证会重置会话，重新固定 CSRF
        return r

    def logout(self):
        self.set_csrf()
        return self.client.post("/logout", data={"csrf_token": "t"},
                                follow_redirects=True)

    def sql(self, q, args=()):
        conn = sqlite3.connect(self.app.config["DB_PATH"])
        conn.row_factory = sqlite3.Row
        rows = [dict(r) for r in conn.execute(q, args).fetchall()]
        conn.commit()
        conn.close()
        return rows

    def make_admin(self, username="rootadmin", password="Passw0rd123"):
        with self.app.app_context():
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?, 'admin')",
                (username, hash_password(password)))
        return username, password


class TestGuestPages(Base):
    """游客可浏览一切公开内容。"""

    PAGES = ["/index", "/about", "/cards", "/issues", "/search", "/login",
             "/register", "/robots.txt", "/sitemap.xml", "/cards?sort=hot",
             "/cards?type=unit&class=infantry&rarity=common", "/issues?status=open"]

    def test_guest_pages_200(self):
        for path in self.PAGES:
            with self.subTest(path=path):
                r = self.client.get(path)
                self.assertEqual(r.status_code, 200, path)

    def test_root_redirects_to_index(self):
        """/ 永久重定向到 /index，外链与搜索引擎不死链。"""
        r = self.client.get("/", follow_redirects=False)
        self.assertEqual(r.status_code, 301)
        self.assertEqual(r.headers["Location"], "/index")
        r = self.client.get("/index")
        self.assertEqual(r.status_code, 200)

    def test_404_page(self):
        r = self.client.get("/definitely-not-a-page")
        self.assertEqual(r.status_code, 404)

    def test_official_cards_visible(self):
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        r = self.client.get("/cards")
        html = r.get_data(as_text=True)
        self.assertIn("步兵", html)
        self.assertIn("轻型坦克", html)

    def test_card_detail_page(self):
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
            cid = db.query("SELECT id FROM cards WHERE game_id='infantry_01'", one=True)["id"]
        r = self.client.get(f"/card/{cid}")
        html = r.get_data(as_text=True)
        self.assertEqual(r.status_code, 200)
        self.assertIn("步兵", html)
        self.assertIn("gcard", html)          # 游戏风格卡面
        self.assertIn("堑壕中的士兵", html)    # flavor_text
        self.assertIn("cost-g", html)         # 经济 G 显示

    def test_sitemap_contains_cards(self):
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        r = self.client.get("/sitemap.xml")
        self.assertIn("/card/1", r.get_data(as_text=True))

    def test_guest_cannot_submit(self):
        r = self.client.get("/cards/new", follow_redirects=False)
        self.assertEqual(r.status_code, 302)  # 重定向到登录
        self.assertIn("/login", r.headers["Location"])

    def test_guest_cannot_admin(self):
        r = self.client.get("/admin", follow_redirects=True)
        self.assertEqual(r.status_code, 403)


class TestAuth(Base):
    def test_register_login_logout(self):
        r = self.register("alice", "Passw0rd123", "alice@example.com")
        self.assertEqual(r.status_code, 200)
        users = self.sql("SELECT * FROM users WHERE username='alice'")
        self.assertEqual(len(users), 1)
        self.assertTrue(users[0]["password_hash"].startswith("$argon2"))
        # 登录
        self.logout()
        r = self.login("alice", "Passw0rd123")
        self.assertIn("欢迎回来", r.get_data(as_text=True))
        # 邮箱也能登录
        self.logout()
        r = self.login("alice@example.com", "Passw0rd123")
        self.assertIn("欢迎回来", r.get_data(as_text=True))
        # 错误密码
        self.logout()
        r = self.login("alice", "wrongpass1")
        self.assertIn("用户名或密码错误", r.get_data(as_text=True))

    def test_duplicate_username(self):
        self.register("bob", "Passw0rd123")
        self.logout()
        self.set_csrf()
        r = self.client.post("/register", data={
            "csrf_token": "t",
            "username": "BOB", "password": "Passw0rd123", "confirm": "Passw0rd123"})
        self.assertIn("已被占用", r.get_data(as_text=True))

    def test_password_strength(self):
        self.set_csrf()
        r = self.client.post("/register", data={
            "csrf_token": "t",
            "username": "carol", "password": "short1", "confirm": "short1"})
        self.assertIn("至少", r.get_data(as_text=True))
        r = self.client.post("/register", data={
            "csrf_token": "t",
            "username": "carol", "password": "onlyletters", "confirm": "onlyletters"})
        self.assertIn("字母和数字", r.get_data(as_text=True))

    def test_csrf_required_on_register(self):
        r = self.client.post("/register", data={
            "username": "dave", "password": "Passw0rd123", "confirm": "Passw0rd123"})
        self.assertEqual(r.status_code, 400)

    def test_login_rate_limit(self):
        self.set_csrf()
        for _ in range(5):
            self.client.post("/login", data={
                "csrf_token": "t", "username": "hacker", "password": "wrong"})
        r = self.client.post("/login", data={
            "csrf_token": "t", "username": "hacker", "password": "wrong"})
        self.assertEqual(r.status_code, 429)

    def test_banned_user_cannot_login_or_browse_session(self):
        self.register("eve", "Passw0rd123")
        self.sql("UPDATE users SET is_banned=1 WHERE username='eve'")
        self.logout()
        r = self.login("eve", "Passw0rd123")
        self.assertIn("用户名或密码错误", r.get_data(as_text=True))

    def test_open_redirect_blocked(self):
        self.register("frank", "Passw0rd123")
        self.logout()
        self.set_csrf()
        r = self.client.post("/login?next=//evil.example.com",
                             data={"csrf_token": "t", "username": "frank",
                                   "password": "Passw0rd123"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(r.headers["Location"], "/index")


class TestSecurity(Base):
    def test_markdown_xss_sanitized(self):
        from app.markdown_utils import render_markdown
        evil = "<script>alert(1)</script>[x](javascript:alert(2)) <img src=x onerror=alert(3)>"
        html = render_markdown(evil)
        self.assertNotIn("<script", html.lower())
        self.assertNotIn("onerror", html.lower())
        self.assertNotIn("javascript:", html.lower())

    def test_comment_xss_sanitized(self):
        self.register("alice", "Passw0rd123")
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
            cid = db.query("SELECT id FROM cards LIMIT 1", one=True)["id"]
            from app.interactions import add_comment
            comment = add_comment(1, "card", cid, "<script>alert(1)</script>**bold**")
            row = db.query("SELECT body_html FROM comments WHERE id=?", (comment,), one=True)
            html = row["body_html"].lower()
            # bleach strip：脚本标签整体移除、内文保留为纯文本 —— 无执行可能
            self.assertNotIn("<script", html)
            self.assertNotIn("alert(1)</script", html)
            self.assertIn("<strong>bold</strong>", html)

    def test_api_requires_login(self):
        r = self.client.post("/api/vote/card/1")
        self.assertEqual(r.status_code, 401)

    def test_api_requires_csrf(self):
        self.register("alice", "Passw0rd123")
        r = self.client.post("/api/vote/card/1", headers={"X-CSRF-Token": "bad"})
        self.assertEqual(r.status_code, 400)

    def test_admin_requires_role(self):
        self.register("mallory", "Passw0rd123")
        for path in ["/admin", "/admin/users", "/admin/cards", "/admin/reports",
                     "/admin/labels", "/admin/github", "/admin/audit"]:
            r = self.client.get(path, follow_redirects=True)
            self.assertEqual(r.status_code, 403, path)

    def test_upload_rejects_non_image(self):
        self.register("alice", "Passw0rd123")
        r = self.client.post("/cards/new", data={
            "csrf_token": "t",
            "name": "恶意卡", "type": "unit", "unit_class": "infantry",
            "art": (io.BytesIO(b"MZ\x90\x00fake-exe"), "evil.exe"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("只支持", r.get_data(as_text=True))

    def test_upload_accepts_png(self):
        self.register("alice", "Passw0rd123")
        r = self.client.post("/cards/new", data={
            "csrf_token": "t",
            "name": "图片卡", "type": "unit", "unit_class": "infantry",
            "cost_g": "30", "cost_k": "1", "attack": "3", "defense": "4",
            "art": (io.BytesIO(png_bytes()), "art.png"),
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("投稿成功", r.get_data(as_text=True))
        row = self.sql("SELECT art_path FROM cards WHERE name='图片卡'")[0]
        self.assertTrue(row["art_path"].startswith("/uploads/cards/"))



class TestVotes(Base):
    def setUp(self):
        super().setUp()
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        self.register("voter", "Passw0rd123")


    def test_vote_toggle_and_count(self):
        h = self.csrf_hdr()
        r1 = self.client.post("/api/vote/card/1", headers=h).get_json()
        self.assertTrue(r1["voted"])
        self.assertEqual(r1["count"], 1)
        # 重复点击 = 取消
        r2 = self.client.post("/api/vote/card/1", headers=h).get_json()
        self.assertFalse(r2["voted"])
        self.assertEqual(r2["count"], 0)
        # 数据库唯一约束存在
        sqls = self.sql("SELECT sql FROM sqlite_master WHERE name='votes'")
        self.assertIn("PRIMARY KEY", sqls[0]["sql"])

    def test_vote_concurrent_unique(self):
        """并发刷票：唯一约束兜底，票数不超 1。"""
        h = self.csrf_hdr()
        for _ in range(5):
            self.client.post("/api/vote/card/1", headers=h)
        # 唯一约束兜底：同一用户对同一目标最多 1 票
        n = self.sql("SELECT COUNT(*) c FROM votes")[0]["c"]
        self.assertLessEqual(n, 1)

    def test_vote_invalid_target(self):
        h = self.csrf_hdr()
        r = self.client.post("/api/vote/card/99999", headers=h)
        self.assertEqual(r.status_code, 404)
        r = self.client.post("/api/vote/hacker/1", headers=h)
        self.assertEqual(r.status_code, 400)


class TestComments(Base):
    def setUp(self):
        super().setUp()
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        self.register("author", "Passw0rd123")


    def test_comment_and_reply(self):
        h = self.csrf_hdr()
        r = self.client.post("/api/comment/card/1", headers=h,
                             data={"body": "顶级评论"}).get_json()
        self.assertTrue(r["ok"])
        r2 = self.client.post("/api/comment/card/1", headers=h,
                              data={"body": "嵌套回复", "parent_id": r["id"]}).get_json()
        self.assertTrue(r2["ok"])
        rows = self.sql("SELECT id, parent_id FROM comments ORDER BY id")
        self.assertEqual(rows[1]["parent_id"], rows[0]["id"])
        # 详情页展示
        html = self.client.get("/card/1").get_data(as_text=True)
        self.assertIn("顶级评论", html)
        self.assertIn("嵌套回复", html)

    def test_edit_permission(self):
        h = self.csrf_hdr()
        cid = self.client.post("/api/comment/card/1", headers=h,
                               data={"body": "原文"}).get_json()["id"]
        self.logout()
        self.register("other", "Passw0rd123")
        r = self.client.post(f"/api/comment/{cid}/edit", headers=h,
                             data={"body": "篡改"})
        self.assertEqual(r.status_code, 403)
        # 作者本人可编辑
        self.logout()
        self.login("author")
        r = self.client.post(f"/api/comment/{cid}/edit", headers=h,
                             data={"body": "改好了"}).get_json()
        self.assertTrue(r["ok"])

    def test_empty_comment_rejected(self):
        h = self.csrf_hdr()
        r = self.client.post("/api/comment/card/1", headers=h, data={"body": "   "})
        self.assertEqual(r.status_code, 400)


class TestCards(Base):
    def setUp(self):
        super().setUp()
        self.register("creator", "Passw0rd123")


    def test_submit_and_edit_own_card(self):
        h = self.csrf_hdr()
        r = self.client.post("/cards/new", headers=h, data={
            "name": " test 卡 ", "type": "unit", "unit_class": "tank",
            "cost_g": "65", "cost_k": "2", "attack": "5", "defense": "5",
            "rarity": "gold", "nation": "neutral",
            "vision_range": "adjacent_8", "attack_range": "adjacent_8",
            "abilities": ["坚守", "突击"], "ability_level_坚守": "2",
            "flavor_text": "测试风味", "description": "**测试** 描述", "tags": "测试, 自制",
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("投稿成功", r.get_data(as_text=True))
        row = self.sql("SELECT * FROM cards WHERE name='test 卡'")[0]
        self.assertEqual(json.loads(row["abilities"]), ["坚守2", "突击"])
        self.assertEqual(row["source"], "community")
        self.assertEqual(row["author_id"], 1)
        # 详情页渲染词条
        html = self.client.get(f"/card/{row['id']}").get_data(as_text=True)
        self.assertIn("坚守2", html)
        self.assertIn("测试风味", html)
        # 编辑
        r = self.client.post(f"/card/{row['id']}/edit", headers=h, data={
            "name": " test 卡 ", "type": "unit", "unit_class": "tank",
            "cost_g": "70", "cost_k": "2", "attack": "6", "defense": "5",
            "rarity": "gold", "nation": "neutral", "flavor_text": "改了",
            "abilities": [], "description": "x", "tags": "",
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("卡牌已更新", r.get_data(as_text=True))
        row2 = self.sql("SELECT attack, cost_g FROM cards WHERE id=?", (row["id"],))[0]
        self.assertEqual(row2["attack"], 6)
        self.assertEqual(row2["cost_g"], 70)

    def test_cannot_edit_others_card(self):
        h = self.csrf_hdr()
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        cid = self.sql("SELECT id FROM cards WHERE source='official'")[0]["id"]
        r = self.client.post(f"/card/{cid}/edit", headers=h, data={
            "name": "篡改", "type": "unit", "unit_class": "tank"})
        self.assertEqual(r.status_code, 403)

    def test_official_card_cannot_be_edited_or_deleted(self):
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        cid = self.sql("SELECT id FROM cards WHERE source='official'")[0]["id"]
        h = self.csrf_hdr()
        r = self.client.post(f"/card/{cid}/delete", headers=h)
        self.assertEqual(r.status_code, 403)

    def test_slug_uniqueness(self):
        h = self.csrf_hdr()
        for i in range(2):
            self.client.post("/cards/new", headers=h, data={
                "name": "同名卡", "type": "unit", "unit_class": "infantry"},
                content_type="multipart/form-data")
        slugs = [r["slug"] for r in self.sql("SELECT slug FROM cards WHERE name='同名卡'")]
        self.assertEqual(len(slugs), 2)
        self.assertNotEqual(slugs[0], slugs[1])

    def test_official_and_community_card_rules(self):
        """官方/自制分离：所有人可投自制；官方仅 admin 可投/编/删。"""
        h = self.csrf_hdr()
        # 1) 普通用户带 source=official 投稿 → 强制 community
        r = self.client.post("/cards/new", headers=h, data={
            "name": "伪装官方卡", "type": "unit", "unit_class": "infantry",
            "source": "official",
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("投稿成功", r.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT source FROM cards WHERE name='伪装官方卡'")[0]["source"],
                         "community")
        # 2) 管理员投稿官方卡 → official
        admin_user, admin_pass = self.make_admin()
        self.logout()
        self.login(admin_user, admin_pass)
        h = self.csrf_hdr()
        r = self.client.post("/cards/new", headers=h, data={
            "name": "官方新卡", "type": "unit", "unit_class": "infantry",
            "source": "official",
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("官方卡牌投稿成功", r.get_data(as_text=True))
        cid = self.sql("SELECT id FROM cards WHERE name='官方新卡'")[0]["id"]
        self.assertEqual(self.sql("SELECT source FROM cards WHERE id=?", (cid,))[0]["source"],
                         "official")
        # 3) 管理员可编辑官方卡
        r = self.client.post(f"/card/{cid}/edit", headers=h, data={
            "name": "官方新卡改", "type": "unit", "unit_class": "infantry",
            "source": "official",
        }, content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("卡牌已更新", r.get_data(as_text=True))
        # 4) 管理员可将自制卡采纳为官方
        self.client.post("/cards/new", headers=h, data={
            "name": "待采纳卡", "type": "unit", "unit_class": "tank"},
            content_type="multipart/form-data")
        promote_id = self.sql("SELECT id FROM cards WHERE name='待采纳卡'")[0]["id"]
        self.client.post(f"/card/{promote_id}/edit", headers=h, data={
            "name": "待采纳卡", "type": "unit", "unit_class": "tank", "source": "official"},
            content_type="multipart/form-data")
        self.assertEqual(self.sql("SELECT source FROM cards WHERE id=?", (promote_id,))[0]["source"],
                         "official")
        # 5) 普通用户不能编辑官方卡
        self.logout()
        self.register("pleb", "Passw0rd123")
        h = self.csrf_hdr()
        r = self.client.post(f"/card/{cid}/edit", headers=h, data={
            "name": "篡改官方", "type": "unit", "unit_class": "infantry"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post(f"/card/{cid}/delete", headers=h)
        self.assertEqual(r.status_code, 403)
        # 6) 列表分区筛选
        html = self.client.get("/cards?source=official").get_data(as_text=True)
        self.assertIn("官方新卡改", html)
        self.assertNotIn("伪装官方卡", html)
        html = self.client.get("/cards?source=community").get_data(as_text=True)
        self.assertIn("伪装官方卡", html)
        self.assertNotIn("官方新卡改", html)


class TestIssues(Base):
    def setUp(self):
        super().setUp()
        self.register("reporter", "Passw0rd123")


    def test_create_and_view_issue(self):
        h = self.csrf_hdr()
        r = self.client.post("/issues/new", headers=h, data={
            "title": "游戏启动时闪退", "body": "## 复现步骤\n1. 启动游戏\n\n## 预期\n正常",
            "game_version": "0.3.0", "sys_info": "Windows 11", "tags": "崩溃",
        }, follow_redirects=True)
        self.assertIn("感谢反馈", r.get_data(as_text=True))
        html = self.client.get("/issue/1").get_data(as_text=True)
        self.assertIn("游戏启动时闪退", html)
        self.assertIn("复现步骤", html)
        self.assertIn("Windows 11", html)
        # 列表可见
        html = self.client.get("/issues").get_data(as_text=True)
        self.assertIn("游戏启动时闪退", html)

    def test_moderator_changes_status(self):
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h, data={
            "title": "某卡牌效果错误", "body": "描述"}, follow_redirects=True)
        self.logout()
        # 普通用户不能改状态
        self.register("pleb", "Passw0rd123")
        r = self.client.post("/issue/1/moderate", headers=h,
                             data={"action": "status", "status": "resolved"})
        self.assertEqual(r.status_code, 403)
        # 管理员可以
        self.logout()
        admin_user, admin_pass = self.make_admin()
        self.login(admin_user, admin_pass)
        r = self.client.post("/issue/1/moderate", headers=h,
                             data={"action": "status", "status": "in_progress"},
                             follow_redirects=True)
        self.assertIn("In Progress", r.get_data(as_text=True))
        status = self.sql("SELECT status FROM issues WHERE id=1")[0]["status"]
        self.assertEqual(status, "in_progress")

    def test_duplicate_flow(self):
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "原始问题标题", "body": "原始"}, follow_redirects=True)
        self.client.post("/issues/new", headers=h,
                         data={"title": "重复的问题标题", "body": "重复"}, follow_redirects=True)
        with self.app.app_context():
            db.execute("UPDATE users SET role='admin' WHERE username='reporter'")
        r = self.client.post("/issue/2/moderate", headers=h,
                             data={"action": "status", "status": "duplicate",
                                   "duplicate_of": "1"}, follow_redirects=True)
        row = self.sql("SELECT status, duplicate_of FROM issues WHERE id=2")[0]
        self.assertEqual(row["status"], "duplicate")
        self.assertEqual(row["duplicate_of"], 1)

    def test_author_can_edit_own_issue(self):
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "我遇到的问题标题", "body": "内容"}, follow_redirects=True)
        r = self.client.post("/issue/1/edit", headers=h, data={
            "title": "我遇到的问题（更新）", "body": "新内容"}, follow_redirects=True)
        self.assertIn("已更新", r.get_data(as_text=True))


class TestAdmin(Base):
    def setUp(self):
        super().setUp()
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        self.admin_user, self.admin_pass = self.make_admin()
        self.login(self.admin_user, self.admin_pass)


    def test_admin_pages_200(self):
        for path in ["/admin", "/admin/users", "/admin/cards", "/admin/issues",
                     "/admin/comments", "/admin/reports", "/admin/labels",
                     "/admin/github", "/admin/audit"]:
            r = self.client.get(path, follow_redirects=True)
            self.assertEqual(r.status_code, 200, path)

    def test_ban_user(self):
        self.logout()
        self.register("baduser", "Passw0rd123")
        uid = self.sql("SELECT id FROM users WHERE username='baduser'")[0]["id"]
        # 重新以管理员身份登录后执行封禁（先登出 baduser）
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        self.client.post(f"/admin/users/{uid}/action", headers=h, data={"action": "ban"})
        self.assertEqual(self.sql("SELECT is_banned FROM users WHERE id=?", (uid,))[0]["is_banned"], 1)
        # 会话被吊销：baduser 的登录态来自 setUp 之后的会话切换，
        # 这里用 baduser 重新登录验证封禁生效
        self.logout()
        self.login("baduser", "Passw0rd123")
        r = self.client.get("/cards/new", follow_redirects=False)
        self.assertEqual(r.status_code, 302)

    def test_hide_card(self):
        h = self.csrf_hdr()
        self.client.post("/admin/cards/1/action", headers=h, data={"action": "hide"})
        self.assertEqual(self.sql("SELECT status FROM cards WHERE id=1")[0]["status"], "hidden")
        # 管理员仍可见
        self.assertEqual(self.client.get("/card/1").status_code, 200)
        # 游客不可见
        self.logout()
        self.assertEqual(self.client.get("/card/1").status_code, 404)
        self.login(self.admin_user, self.admin_pass)
        self.client.post("/admin/cards/1/action", headers=h, data={"action": "show"})
        self.assertEqual(self.client.get("/card/1").status_code, 200)

    def test_report_flow(self):
        # 用户举报
        self.logout()
        self.register("rpt", "Passw0rd123")
        h = self.csrf_hdr()
        r = self.client.post("/api/report", headers=h,
                             data={"target_type": "card", "target_id": 1,
                                   "reason": "内容不当"}).get_json()
        self.assertTrue(r["ok"])
        # 重复举报被拒
        r2 = self.client.post("/api/report", headers=h,
                              data={"target_type": "card", "target_id": 1, "reason": "again"})
        self.assertEqual(r2.status_code, 400)
        # 管理员处理
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        html = self.client.get("/admin/reports").get_data(as_text=True)
        self.assertIn("内容不当", html)
        rid = self.sql("SELECT id FROM reports")[0]["id"]
        self.client.post(f"/admin/reports/{rid}/action", headers=h,
                         data={"action": "dismiss"})
        self.assertEqual(self.sql("SELECT status FROM reports")[0]["status"], "dismissed")

    def test_batch_users(self):
        """批量封禁 / 角色变更 / 不能操作自己 / 非法 action。"""
        self.logout()
        self.register("batchu1", "Passw0rd123")
        self.logout()
        self.register("batchu2", "Passw0rd123")
        uid1 = self.sql("SELECT id FROM users WHERE username='batchu1'")[0]["id"]
        uid2 = self.sql("SELECT id FROM users WHERE username='batchu2'")[0]["id"]
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "ban", "ids": [str(uid1), str(uid2)]},
                             follow_redirects=True)
        self.assertIn("批量操作完成", r.get_data(as_text=True))
        states = {row["username"]: row["is_banned"] for row in self.sql(
            "SELECT username, is_banned FROM users WHERE username LIKE 'batchu%'")}
        self.assertEqual(states, {"batchu1": 1, "batchu2": 1})
        # 不能对自己批量操作
        me = self.sql("SELECT id FROM users WHERE username=?", (self.admin_user,))[0]["id"]
        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "ban", "ids": [str(me)]}, follow_redirects=True)
        self.assertIn("跳过 1", r.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT is_banned FROM users WHERE id=?", (me,))[0]["is_banned"], 0)
        # 非法 action 被拒绝
        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "hack", "ids": ["1"]}, follow_redirects=True)
        self.assertIn("请先勾选", r.get_data(as_text=True))
        # 批量角色变更
        self.client.post("/admin/users/batch", headers=h,
                         data={"action": "set_moderator", "ids": [str(uid1)]},
                         follow_redirects=True)
        self.assertEqual(self.sql("SELECT role FROM users WHERE id=?", (uid1,))[0]["role"],
                         "moderator")

    def test_batch_cards_hide_show_delete(self):
        h = self.csrf_hdr()
        r = self.client.post("/admin/cards/batch", headers=h,
                             data={"action": "hide", "ids": ["1", "2"]},
                             follow_redirects=True)
        self.assertIn("批量操作完成", r.get_data(as_text=True))
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM cards WHERE id IN (1,2) AND status='hidden'")[0]["c"], 2)
        # 隐藏后游客不可见
        self.logout()
        self.assertEqual(self.client.get("/card/1").status_code, 404)
        # 批量恢复
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        self.client.post("/admin/cards/batch", headers=h,
                         data={"action": "show", "ids": ["1"]}, follow_redirects=True)
        self.assertEqual(self.client.get("/card/1").status_code, 200)
        # 批量删除
        self.client.post("/admin/cards/batch", headers=h,
                         data={"action": "delete", "ids": ["2"]}, follow_redirects=True)
        self.assertEqual(self.sql("SELECT COUNT(*) c FROM cards WHERE id=2")[0]["c"], 0)
        # 空勾选被拒绝
        r = self.client.post("/admin/cards/batch", headers=h,
                             data={"action": "delete"}, follow_redirects=True)
        self.assertIn("请先勾选", r.get_data(as_text=True))

    def test_batch_issues_status_and_delete(self):
        self.logout()
        self.register("batchrep", "Passw0rd123")
        h = self.csrf_hdr()
        for t in ("批量问题一", "批量问题二"):
            self.client.post("/issues/new", headers=h, data={"title": t, "body": "x"},
                             follow_redirects=True)
        ids = [str(r["id"]) for r in self.sql(
            "SELECT id FROM issues WHERE title LIKE '批量问题%' ORDER BY id")]
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        r = self.client.post("/admin/issues/batch", headers=h,
                             data={"action": "set_in_progress", "ids": ids},
                             follow_redirects=True)
        self.assertIn("批量操作完成", r.get_data(as_text=True))
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM issues WHERE title LIKE '批量问题%' "
                     "AND status='in_progress'")[0]["c"], 2)
        # 批量删除其中一个
        self.client.post("/admin/issues/batch", headers=h,
                         data={"action": "delete", "ids": [ids[0]]}, follow_redirects=True)
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM issues WHERE title LIKE '批量问题%'")[0]["c"], 1)

    def test_batch_comments_and_reports(self):
        # 准备：用户发两条评论 + 两条举报
        self.logout()
        self.register("batchc", "Passw0rd123")
        h = self.csrf_hdr()
        c1 = self.client.post("/api/comment/card/1", headers=h,
                              data={"body": "待删评论一"}).get_json()["id"]
        c2 = self.client.post("/api/comment/card/1", headers=h,
                              data={"body": "待删评论二"}).get_json()["id"]
        self.client.post("/api/report", headers=h,
                         data={"target_type": "card", "target_id": 1, "reason": "批量举报一"})
        self.client.post("/api/report", headers=h,
                         data={"target_type": "card", "target_id": 2, "reason": "批量举报二"})
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        # 批量删评论
        r = self.client.post("/admin/comments/batch", headers=h,
                             data={"action": "delete", "ids": [str(c1), str(c2)]},
                             follow_redirects=True)
        self.assertIn("批量操作完成", r.get_data(as_text=True))
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM comments WHERE id IN (?,?) AND is_deleted=1",
                     (c1, c2))[0]["c"], 2)
        # 批量恢复
        self.client.post("/admin/comments/batch", headers=h,
                         data={"action": "restore", "ids": [str(c1)]}, follow_redirects=True)
        self.assertEqual(
            self.sql("SELECT is_deleted FROM comments WHERE id=?", (c1,))[0]["is_deleted"], 0)
        # 批量处理举报（隐藏内容并处理）
        rids = [str(r["id"]) for r in self.sql("SELECT id FROM reports ORDER BY id")]
        r = self.client.post("/admin/reports/batch", headers=h,
                             data={"action": "hide_content", "ids": rids},
                             follow_redirects=True)
        self.assertIn("批量操作完成", r.get_data(as_text=True))
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM reports WHERE status='resolved'")[0]["c"], 2)
        self.assertEqual(self.sql("SELECT status FROM cards WHERE id=1")[0]["status"], "hidden")
        # 带筛选参数的列表页正常
        r = self.client.get("/admin/reports?status=resolved")
        self.assertEqual(r.status_code, 200)

    def test_admin_filters(self):
        """各列表页筛选参数生效且页面 200。"""
        self.csrf_hdr()
        cases = [
            "/admin/users?role=user&status=active",
            "/admin/users?role=admin",
            "/admin/users?q=不存在的用户xyz",
            "/admin/cards?source=official&status=visible",
            "/admin/cards?source=community&rarity=gold",
            "/admin/cards?type=unit&class=infantry",
            "/admin/issues?priority=high",
            "/admin/issues?q=找不到的问题",
            "/admin/comments?target_type=card&state=active",
            "/admin/comments?q=找不到的内容zzz",
            "/admin/reports?status=open&target_type=card",
            "/admin/github?status=pending",
            "/admin/audit?action=login",
            "/admin/audit?q=找不到的日志qqq",
        ]
        for path in cases:
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)

    def test_batch_requires_admin(self):
        """非管理员不能调用批量端点。"""
        self.logout()
        self.register("notadmin", "Passw0rd123")
        h = self.csrf_hdr()
        for path in ("/admin/users/batch", "/admin/cards/batch", "/admin/issues/batch",
                     "/admin/comments/batch", "/admin/reports/batch", "/admin/labels/batch"):
            r = self.client.post(path, headers=h, data={"action": "delete", "ids": ["1"]})
            self.assertEqual(r.status_code, 403, path)

    def test_card_view_modes(self):
        """前端三视图切换 + Cookie 记住偏好。"""
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
        # 默认卡片视图
        html = self.client.get("/cards").get_data(as_text=True)
        self.assertIn("card-grid", html)
        self.assertNotIn("card-rows", html)
        # 列表视图
        html = self.client.get("/cards?view=list").get_data(as_text=True)
        self.assertIn("card-rows", html)
        self.assertIn("card-row", html)
        # Cookie 已记住偏好：不带参数仍为列表视图
        html = self.client.get("/cards").get_data(as_text=True)
        self.assertIn("card-rows", html)
        # 图标墙视图
        html = self.client.get("/cards?view=compact").get_data(as_text=True)
        self.assertIn("view-compact", html)
        # 非法 view 回退到 Cookie 偏好（compact）
        html = self.client.get("/cards?view=bogus").get_data(as_text=True)
        self.assertIn("view-compact", html)
        # 切回 grid 并验证 Cookie 覆盖
        self.client.get("/cards?view=grid")
        html = self.client.get("/cards").get_data(as_text=True)
        self.assertIn("card-grid", html)
        self.assertNotIn("view-compact", html)

    def test_admin_card_view_modes(self):
        """后台两视图：detailed 带 gcard 预览列，compact 隐藏。"""
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES ('viewadmin', ?, 'admin')",
                (hash_password("Passw0rd123"),))
        self.login("viewadmin", "Passw0rd123")
        html = self.client.get("/admin/cards").get_data(as_text=True)
        self.assertIn("预览", html)
        self.assertIn("gcard", html)
        # 紧凑视图：无预览列、无 gcard 渲染
        html = self.client.get("/admin/cards?view=compact").get_data(as_text=True)
        self.assertNotIn("<th>预览</th>", html)
        self.assertNotIn("gcard", html)
        self.assertIn("mini-inline", html)
        # Cookie 记住紧凑视图
        html = self.client.get("/admin/cards").get_data(as_text=True)
        self.assertNotIn("<th>预览</th>", html)
        # 非法 view 回退
        html = self.client.get("/admin/cards?view=bogus").get_data(as_text=True)
        self.assertNotIn("<th>预览</th>", html)

    def test_nav_page(self):
        """/nav 枢纽页：公开访问，包含「去 1914」入口。"""
        r = self.client.get("/nav")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("导航", html)
        self.assertIn("1914", html)
        self.assertIn('href="/index"', html)
        self.assertIn("hub-card", html)

    def test_audit_log_records(self):
        h = self.csrf_hdr()
        self.client.post("/admin/cards/1/action", headers=h, data={"action": "hide"})
        logs = self.sql("SELECT action FROM audit_log")
        self.assertTrue(any(l["action"] == "card_hide" for l in logs))


class TestGameCardSync(Base):
    def test_import_matches_game_data(self):
        with self.app.app_context():
            from app.card_sync import import_official_cards
            result = import_official_cards()
            self.assertEqual(result["created"], 16)
            # 二次导入 = 全部更新
            result2 = import_official_cards()
            self.assertEqual(result2["updated"], 16)
            # 字段精确对照游戏 JSON
            game = json.loads((Path(__file__).parent.parent / "seed" / "cards" / "tank_02.json")
                              .read_text(encoding="utf-8"))
            row = db.query("SELECT * FROM cards WHERE game_id='tank_02'", one=True)
            self.assertEqual(row["name"], game["name"])
            self.assertEqual(row["cost_g"], game["cost_g"])
            self.assertEqual(row["cost_k"], game["cost_k"])
            self.assertEqual(row["attack"], game["attack"])
            self.assertEqual(row["defense"], game["defense"])
            self.assertEqual(json.loads(row["abilities"]), game["abilities"])
            self.assertEqual(row["rarity"], game["rarity"])
            self.assertEqual(row["flavor_text"], game["flavor_text"])
            self.assertEqual(row["source"], "official")

    def test_import_falls_back_to_seed(self):
        """游戏项目不存在时，导入自动回退到内置 seed/cards 副本。"""
        with self.app.app_context():
            from app.card_sync import import_official_cards
            self.app.config["GAME_PROJECT_PATH"] = "Z:/no/such/dir"
            result = import_official_cards()
            self.assertEqual(result["created"], 16)
            row = db.query("SELECT game_id, source FROM cards WHERE game_id='tank_01'", one=True)
            self.assertEqual(row["game_id"], "tank_01")
            self.assertEqual(row["source"], "official")

    def test_all_ability_tokens_resolve(self):
        with self.app.app_context():
            from app.card_sync import import_official_cards
            from app.gameconstants import ability_name, ABILITIES
            import_official_cards()
            rows = db.query("SELECT abilities FROM cards")
            for r in rows:
                for token in json.loads(r["abilities"]):
                    self.assertIn(ability_name(token), ABILITIES, token)


class TestServerStatus(Base):
    def test_status_page(self):
        """/status 公开访问：网站区有真实指标，游戏服区显示不可用。"""
        r = self.client.get("/status")
        self.assertEqual(r.status_code, 200)
        html = r.get_data(as_text=True)
        self.assertIn("网站服务器", html)
        self.assertIn("游戏服务器", html)
        self.assertIn("不可用", html)
        self.assertIn("badge-olive", html)      # web 在线

    def test_status_json(self):
        data = self.client.get("/api/status").get_json()
        self.assertEqual(data["web"]["status"], "up")
        self.assertEqual(data["web"]["db"], "ok")
        self.assertIn("uptime_sec", data["web"])
        self.assertFalse(data["game"]["available"])
        self.assertIsNone(data["game"]["matches"])

    def test_heartbeat_disabled_without_token(self):
        r = self.client.post("/api/game-server/heartbeat",
                             headers={"X-Game-Token": "x"}, json={"status": "online"})
        self.assertEqual(r.status_code, 403)

    def test_heartbeat_flow(self):
        self.app.config["GAME_SERVER_TOKEN"] = "gsec"
        # 错误 token
        r = self.client.post("/api/game-server/heartbeat",
                             headers={"X-Game-Token": "wrong"}, json={"status": "online"})
        self.assertEqual(r.status_code, 403)
        # 正确 token → 状态点亮
        r = self.client.post("/api/game-server/heartbeat",
                             headers={"X-Game-Token": "gsec"},
                             json={"status": "online", "matches": 3, "players": 8,
                                   "max_matches": 10, "max_players": 50,
                                   "cpu": 42.5, "mem": 61, "version": "0.1.0"})
        self.assertEqual(r.status_code, 200)
        data = self.client.get("/api/status").get_json()
        self.assertTrue(data["game"]["available"])
        self.assertEqual(data["game"]["matches"], 3)
        self.assertEqual(data["game"]["players"], 8)
        self.assertEqual(data["game"]["max_matches"], 10)
        self.assertEqual(data["game"]["max_players"], 50)
        self.assertEqual(data["game"]["status"], "在线")
        html = self.client.get("/status").get_data(as_text=True)
        self.assertIn("在线", html)
        self.assertIn("数据更新", html)  # "心跳"表述已替换



if __name__ == "__main__":
    unittest.main(verbosity=2)
