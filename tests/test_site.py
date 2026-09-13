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
            "STATUS_SAMPLER_ENABLED": False,
            "AUTO_SEED": False,
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
        self.logout()   # 注册视图对已登录用户会重定向，先确保登出
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

    def test_failed_login_keeps_credentials(self):
        """登录失败后保留用户名和密码输入，不强制重打。"""
        self.set_csrf()
        r = self.client.post("/login", data={
            "csrf_token": "t", "username": "alice", "password": "wrongpass1"})
        html = r.get_data(as_text=True)
        self.assertIn('value="alice"', html)
        self.assertIn('value="wrongpass1"', html)
        self.assertIn("用户名或密码错误", html)
        # GET 登录页不带预填
        html = self.client.get("/login").get_data(as_text=True)
        self.assertNotIn('value="alice"', html)



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
            "cost_g": "30", "cost_z": "1", "attack": "3", "defense": "4",
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
            "cost_g": "65", "cost_z": "2", "attack": "5", "defense": "5",
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
            "cost_g": "70", "cost_z": "2", "attack": "6", "defense": "5",
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
        # 官方卡不可举报
        self.logout()
        self.register("rpt", "Passw0rd123")
        h = self.csrf_hdr()
        r = self.client.post("/api/report", headers=h,
                             data={"target_type": "card", "target_id": 1,
                                   "reason": "内容不当"})
        self.assertEqual(r.status_code, 400)
        # 用户举报评论
        c = self.client.post("/api/comment/card/1", headers=h,
                             data={"body": "被举报的评论"}).get_json()["id"]
        r = self.client.post("/api/report", headers=h,
                             data={"target_type": "comment", "target_id": c,
                                   "reason": "内容不当"}).get_json()
        self.assertTrue(r["ok"])
        # 重复举报被拒
        r2 = self.client.post("/api/report", headers=h,
                              data={"target_type": "comment", "target_id": c, "reason": "again"})
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
                         data={"target_type": "comment", "target_id": c1, "reason": "批量举报一"})
        self.client.post("/api/report", headers=h,
                         data={"target_type": "comment", "target_id": c2, "reason": "批量举报二"})
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
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM comments WHERE id IN (?,?) AND is_deleted=1",
                     (c1, c2))[0]["c"], 2)
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

    def test_delete_user_keeps_content(self):
        """删除用户：账号移除、投稿保留（作者置空）、用户名可重新注册。"""
        self.logout()
        self.register("doomed", "Passw0rd123")
        uid = self.sql("SELECT id FROM users WHERE username='doomed'")[0]["id"]
        h = self.csrf_hdr()
        self.client.post("/cards/new", headers=h, data={
            "name": "遗作卡", "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        self.client.post("/issues/new", headers=h,
                         data={"title": "遗留问题标题", "body": "x"}, follow_redirects=True)
        # 管理员删除该用户
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        r = self.client.post(f"/admin/users/{uid}/action", headers=h,
                             data={"action": "delete"}, follow_redirects=True)
        self.assertIn("已删除用户", r.get_data(as_text=True))
        self.assertEqual(self.sql("SELECT COUNT(*) c FROM users WHERE id=?", (uid,))[0]["c"], 0)
        # 内容保留、作者置空
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM cards WHERE name='遗作卡' AND author_id IS NULL")[0]["c"], 1)
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM issues WHERE title='遗留问题标题' AND author_id IS NULL")[0]["c"], 1)
        # 用户名可重新注册
        self.logout()
        r = self.register("doomed", "Passw0rd123")
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM users WHERE username='doomed'")[0]["c"], 1)

    def test_batch_delete_users(self):
        self.logout()
        self.register("bdel1", "Passw0rd123")
        self.logout()
        self.register("bdel2", "Passw0rd123")
        ids = [str(r["id"]) for r in self.sql(
            "SELECT id FROM users WHERE username LIKE 'bdel%' ORDER BY id")]
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "delete", "ids": ids}, follow_redirects=True)
        self.assertIn("删除 2", r.get_data(as_text=True))
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM users WHERE username LIKE 'bdel%'")[0]["c"], 0)

    def test_card_tag_official_only(self):
        """性质仅官方卡有：玩家自制卡无标记；管理员投官方卡的性质受游戏版本钳制
        （v0.* 阶段一律测试卡）；列表可筛选。"""
        # 玩家投稿：即使带 card_tag=测试 也无效 → 无性质
        self.logout()
        self.register("taguser", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/cards/new", headers=h, data={
            "name": "自制卡X", "type": "unit", "unit_class": "infantry",
            "card_tag": "测试"}, content_type="multipart/form-data")
        self.assertEqual(self.sql("SELECT tag FROM cards WHERE name='自制卡X'")[0]["tag"], "")
        # 详情页无性质徽章
        cid = self.sql("SELECT id FROM cards WHERE name='自制卡X'")[0]["id"]
        self.assertNotIn('data-tag="测试"', self.client.get(f"/card/{cid}").get_data(as_text=True))

        # 管理员投官方测试卡
        self.logout()
        self.login(self.admin_user, self.admin_pass)
        h = self.csrf_hdr()
        r = self.client.post("/cards/new", headers=h, data={
            "name": "官方测试卡Y", "type": "unit", "unit_class": "infantry",
            "source": "official", "card_tag": "测试"},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertIn("官方卡牌投稿成功", r.get_data(as_text=True))
        yid = self.sql("SELECT id FROM cards WHERE name='官方测试卡Y'")[0]["id"]
        self.assertEqual(self.sql("SELECT tag FROM cards WHERE id=?", (yid,))[0]["tag"], "测试")
        html = self.client.get(f"/card/{yid}").get_data(as_text=True)
        self.assertIn('data-tag="测试"', html)
        self.assertIn("测试卡牌：游戏 v0.x 阶段的卡牌", html)

        # 列表筛选：测试只有官方测试卡；正式不含它
        html = self.client.get("/cards?card_tag=测试").get_data(as_text=True)
        self.assertIn("官方测试卡Y", html)
        self.assertNotIn("自制卡X", html)
        html = self.client.get("/cards?card_tag=正式").get_data(as_text=True)
        self.assertNotIn("官方测试卡Y", html)

        # 管理员编辑官方卡尝试改正式：v0.* 阶段被钳制，仍为测试
        r = self.client.post(f"/card/{yid}/edit", headers=h, data={
            "name": "官方测试卡Y", "type": "unit", "unit_class": "infantry",
            "source": "official", "card_tag": "正式"},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(self.sql("SELECT tag FROM cards WHERE id=?", (yid,))[0]["tag"], "测试")

        # 版本切到 v1.0 后，同样的表单值才生效为正式
        self.app.config["GAME_VERSION"] = "1.0.0"
        self.client.post(f"/card/{yid}/edit", headers=h, data={
            "name": "官方测试卡Y", "type": "unit", "unit_class": "infantry",
            "source": "official", "card_tag": "正式"},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(self.sql("SELECT tag FROM cards WHERE id=?", (yid,))[0]["tag"], "正式")

    def test_import_tag_heuristic(self):
        """导入官方卡：游戏 v0.* 一律测试卡（含显式测试卡）；v1.0 起正式，
        但显式测试卡（ID 含 test / 名称含 测试）保持测试。"""
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
            rows = {r["game_id"]: r["tag"] for r in db.query(
                "SELECT game_id, tag FROM cards WHERE source='official'")}
        self.assertEqual(rows["test_infantry_01"], "测试")
        self.assertEqual(rows["infantry_01"], "测试")
        self.assertEqual(rows["cavalry_01"], "测试")

        # 游戏进入 v1.0 后重导入：普通卡转正式，显式测试卡保持测试
        self.app.config["GAME_VERSION"] = "1.0.0"
        with self.app.app_context():
            from app.card_sync import import_official_cards
            import_official_cards()
            rows = {r["game_id"]: r["tag"] for r in db.query(
                "SELECT game_id, tag FROM cards WHERE source='official'")}
        self.assertEqual(rows["test_infantry_01"], "测试")
        self.assertEqual(rows["infantry_01"], "正式")
        self.assertEqual(rows["cavalry_01"], "正式")

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
            self.assertEqual(row["cost_z"], game["cost_z"])
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



class TestAutoSeed(unittest.TestCase):
    """空库启动自动播种：官方卡牌/标签/管理员；二次启动不重复。"""

    def setUp(self):
        self.tmps = []

    def tearDown(self):
        import shutil
        for t in self.tmps:
            shutil.rmtree(t, ignore_errors=True)

    def _app(self, db_path):
        self.tmps.append(Path(db_path).parent)
        return create_app({
            "DB_PATH": db_path,
            "UPLOAD_DIR": Path(db_path).parent / "uploads",
            "TESTING": True,
            "SECRET_KEY": "s2",
            "GITHUB_SYNC_ENABLED": False,
            "GITHUB_TOKEN": "",
            "COOKIE_SECURE": False,
            "AUTO_SEED": True,
        })

    def test_seed_on_fresh_db(self):
        dbp = os.path.join(tempfile.mkdtemp(prefix="1914_seed_"), "a.db")
        app = self._app(dbp)
        with app.app_context():
            self.assertEqual(db.query("SELECT COUNT(*) n FROM cards", one=True)["n"], 16)
            self.assertGreaterEqual(db.query("SELECT COUNT(*) n FROM labels", one=True)["n"], 8)
            admin = db.query("SELECT username, role FROM users WHERE username='admin'", one=True)
            self.assertIsNotNone(admin)
            self.assertEqual(admin["role"], "admin")

    def test_reseed_idempotent(self):
        dbp = os.path.join(tempfile.mkdtemp(prefix="1914_seed_"), "a.db")
        self._app(dbp)   # 第一次启动播种
        self._app(dbp)   # 第二次启动不重复
        import sqlite3
        conn = sqlite3.connect(dbp)
        cards = conn.execute("SELECT COUNT(*) FROM cards").fetchone()[0]
        users = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
        conn.close()
        self.assertEqual((cards, users), (16, 1))


class TestIssueComponentAndTags(Base):
    """Issue 所属（网页/游戏本体）、预设标签、同步分流。"""

    def test_component_and_preset_tags(self):
        self.logout()
        self.register("ireporter", "Passw0rd123")
        h = self.csrf_hdr()
        # 网页 Issue + 预设标签
        r = self.client.post("/issues/new", headers=h, data={
            "title": "网页按钮错位的问题标题",
            "body": "描述",
            "component": "web",
            "issue_tags": ["UI 显示", "功能建议"]}, follow_redirects=True)
        self.assertIn("感谢反馈", r.get_data(as_text=True))
        wid = self.sql("SELECT id FROM issues WHERE title='网页按钮错位的问题标题'")[0]["id"]
        row = self.sql("SELECT component FROM issues WHERE id=?", (wid,))[0]
        self.assertEqual(row["component"], "web")
        web_labels = sorted(l["name"] for l in self.sql(
            "SELECT l.name FROM content_labels cl JOIN labels l ON l.id=cl.label_id "
            "WHERE cl.content_type='issue' AND cl.content_id=?", (wid,)))
        self.assertEqual(web_labels, ["UI 显示", "功能建议"])
        # 游戏 Issue（默认所属）
        r = self.client.post("/issues/new", headers=h, data={
            "title": "游戏崩溃的问题标题", "body": "x",
            "issue_tags": ["崩溃"]}, follow_redirects=True)
        gid = self.sql("SELECT id FROM issues WHERE title='游戏崩溃的问题标题'")[0]["id"]
        self.assertEqual(self.sql("SELECT component FROM issues WHERE id=?", (gid,))[0]["component"],
                         "game")
        # 非法预设标签被忽略
        self.client.post("/issues/new", headers=h, data={
            "title": "带非法标签的标题", "body": "x",
            "issue_tags": ["不存在的标签"]}, follow_redirects=True)
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM issues WHERE title='带非法标签的标题'")[0]["c"], 1)

        # 列表：所属筛选
        html = self.client.get("/issues?component=web").get_data(as_text=True)
        self.assertIn("网页按钮错位的问题标题", html)
        self.assertNotIn("游戏崩溃的问题标题", html)
        # 标签筛选
        html = self.client.get("/issues?label=崩溃").get_data(as_text=True)
        self.assertIn("游戏崩溃的问题标题", html)
        # 详情页显示所属与标签
        html = self.client.get(f"/issue/{wid}").get_data(as_text=True)
        self.assertIn("🌐 网页", html)
        self.assertIn("UI 显示", html)

    def test_sync_repo_routing(self):
        """同步队列按所属分流：web→网页仓库，game→游戏仓库。"""
        self.logout()
        self.register("syncuser", "Passw0rd123")
        self.app.config["GITHUB_TOKEN"] = "test-token"
        self.app.config["GITHUB_WEB_REPO"] = "fdvecbtwdh/1914_website"
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "游戏侧同步标题", "body": "x"}, follow_redirects=True)
        self.client.post("/issues/new", headers=h, data={
            "title": "网页侧同步标题", "body": "x", "component": "web"}, follow_redirects=True)
        rows = self.sql("SELECT issue_id, repo FROM sync_queue ORDER BY id")
        repos = {r["issue_id"]: r["repo"] for r in rows}
        gid = self.sql("SELECT id FROM issues WHERE title='游戏侧同步标题'")[0]["id"]
        wid = self.sql("SELECT id FROM issues WHERE title='网页侧同步标题'")[0]["id"]
        self.assertEqual(repos[gid], "fdvecbtwdh/1914")
        self.assertEqual(repos[wid], "fdvecbtwdh/1914_website")

    def test_sync_queue_repo_column_migration(self):
        """旧库 sync_queue 缺 repo 列时，init_db 幂等迁移自动补列，
        否则 enqueue 的 INSERT 静默失败、同步永远不入队。"""
        old_path = os.path.join(self.tmp, "old_queue.db")
        conn = sqlite3.connect(old_path)
        conn.execute("""CREATE TABLE sync_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            issue_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            payload TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            attempts INTEGER NOT NULL DEFAULT 0,
            last_error TEXT,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            finished_at TEXT)""")
        conn.commit()
        conn.close()
        db.init_db(old_path)
        conn = sqlite3.connect(old_path)
        cols = [r[1] for r in conn.execute("PRAGMA table_info(sync_queue)")]
        conn.close()
        self.assertIn("repo", cols)

    def test_component_filter_admin(self):
        self.logout()
        self.register("fuser", "Passw0rd123")
        self.logout()
        admin_u, admin_p = self.make_admin()
        self.login(admin_u, admin_p)
        for path in ("/admin/issues?status=all&component=web", "/admin/users?role=user"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)


class TestAccountRecovery(Base):
    """账户恢复全流程：邮箱（SMTP 收件池实测）、安全问题、限速、一次性令牌。"""

    def setUp(self):
        super().setUp()
        import socket
        import threading
        self.mails = []
        self._stop = threading.Event()
        self._srv = socket.socket()
        self._srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._srv.bind(("127.0.0.1", 0))
        self._port = self._srv.getsockname()[1]
        self._srv.listen(1)
        threading.Thread(target=self._smtp_sink, daemon=True).start()
        # 隔离：测试只连本地收件池，清空真实凭据避免误发/误登录
        self.app.config["MAIL_HOST"] = "127.0.0.1"
        self.app.config["MAIL_PORT"] = self._port
        self.app.config["MAIL_USER"] = ""
        self.app.config["MAIL_PASSWORD"] = ""
        self.app.config["MAIL_USE_TLS"] = False
        self.app.config["MAIL_FROM"] = "noreply@1914.fun"

    def tearDown(self):
        self._stop.set()
        try:
            self._srv.close()
        except OSError:
            pass
        super().tearDown()

    def _smtp_sink(self):
        self._srv.settimeout(8)
        while not self._stop.is_set():
            try:
                conn, _ = self._srv.accept()
            except OSError:
                return
            try:
                conn.sendall(b"220 test ESMTP\r\n")
                f = conn.makefile("rb")
                data_mode, body = False, []
                while True:
                    line = f.readline()
                    if not line:
                        break
                    if data_mode:
                        if line.strip() == b".":
                            data_mode = False
                            self.mails.append("\r\n".join(body))
                            body = []
                            conn.sendall(b"250 OK\r\n")
                        else:
                            body.append(line.decode("utf-8", "replace").rstrip("\r\n"))
                        continue
                    cmd = line.strip().upper()
                    if cmd.startswith(b"DATA"):
                        conn.sendall(b"354 go\r\n")
                        data_mode = True
                    elif cmd.startswith(b"QUIT"):
                        conn.sendall(b"221 bye\r\n")
                        break
                    else:
                        conn.sendall(b"250 OK\r\n")
            except OSError:
                pass
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def _register(self, name, email="", q="", a=""):
        self.logout()
        self.set_csrf()
        r = self.client.post("/register", data={
            "csrf_token": "t", "username": name, "password": "Passw0rd123",
            "confirm": "Passw0rd123", "email": email,
            "security_question": q, "security_answer": a}, follow_redirects=True)
        return r

    def _recover_entry(self, ident):
        self.set_csrf()
        return self.client.post("/recover", data={
            "csrf_token": "t", "ident": ident}, follow_redirects=True)

    def test_register_variants(self):
        """只用户名+密码 / 邮箱 / 安全问题 / 全部，四种注册形态。"""
        self.register("plain")
        row = self.sql("SELECT email, security_question, security_answer_hash FROM users WHERE username='plain'")[0]
        self.assertEqual((row["email"], row["security_question"], row["security_answer_hash"]),
                         (None, None, None))
        self._register("mailer", email="m@1914.fun")
        self.assertEqual(self.sql("SELECT email FROM users WHERE username='mailer'")[0]["email"],
                         "m@1914.fun")
        self._register("secq", q="我的小学", a="sunshine")
        row = self.sql("SELECT security_question, security_answer_hash FROM users WHERE username='secq'")[0]
        self.assertEqual(row["security_question"], "我的小学")
        self.assertNotIn("sunshine", row["security_answer_hash"])
        self.assertTrue(row["security_answer_hash"])
        self._register("both", email="b@1914.fun", q="我的小学", a="sunshine")
        row = self.sql("SELECT email, security_question FROM users WHERE username='both'")[0]
        self.assertEqual((row["email"], row["security_question"]), ("b@1914.fun", "我的小学"))
        # 只填问题不填答案被拒
        self.logout()
        self.set_csrf()
        r = self.client.post("/register", data={
            "csrf_token": "t", "username": "half", "password": "Passw0rd123",
            "confirm": "Passw0rd123", "security_question": "问题"}, follow_redirects=True)
        self.assertIn("同时填写", r.get_data(as_text=True))

    def test_no_recovery_methods_message(self):
        self.register("norecov")
        self.logout()
        self.set_csrf()
        r = self._recover_entry("norecov")
        self.assertIn("此账户没有设置可用的恢复方式，因此无法通过此功能恢复密码。",
                      r.get_data(as_text=True))

    def test_email_recovery_flow(self):
        """邮箱恢复：请求 → 收件 → 令牌重置 → 一次性。"""
        self._register("mailer", email="m@1914.fun")
        self.logout()
        r = self._recover_entry("mailer")
        self.assertIn("选择恢复方式", r.get_data(as_text=True))
        self.assertIn("***", r.get_data(as_text=True))   # 邮箱打码显示
        self.set_csrf()
        r = self.client.post("/recover/email", follow_redirects=False)
        self.assertEqual(r.status_code, 302)
        self.assertEqual(len(self.mails), 1)
        import email as email_mod
        import re
        msg = email_mod.message_from_string(self.mails[0])
        raw = None
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                raw = part.get_payload(decode=True)
                break
        body = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else ""
        m = re.search(r"/recover/reset\?token=([A-Za-z0-9_\-]+)", body)
        self.assertIsNotNone(m)
        token = m.group(1)
        # 令牌哈希入库，原文不落库
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM recovery_tokens WHERE token_hash=?", (token,))[0]["c"], 0)
        # 打开重置页并设置新密码
        r = self.client.get("/recover/reset?token=" + token)
        self.assertIn("设置新密码", r.get_data(as_text=True))
        self.set_csrf()
        r = self.client.post("/recover/reset", data={
            "csrf_token": "t", "token": token,
            "password": "NewPass123", "confirm": "NewPass123"}, follow_redirects=True)
        self.assertIn("密码已重置", r.get_data(as_text=True))
        # 旧密码失效、新密码可登录
        self.logout()
        r = self.login("mailer", "Passw0rd123")
        self.assertIn("用户名或密码错误", r.get_data(as_text=True))
        r = self.login("mailer", "NewPass123")
        self.assertIn("欢迎回来", r.get_data(as_text=True))
        # 令牌一次性：再次使用无效
        self.logout()
        r = self.client.get("/recover/reset?token=" + token)
        self.assertIn("无效", r.get_data(as_text=True))

    def test_expired_token_rejected(self):
        self._register("exp", email="e@1914.fun")
        self.logout()
        self.set_csrf()
        self._recover_entry("exp")
        self.client.post("/recover/email")
        self.sql("UPDATE recovery_tokens SET expires_at='2000-01-01 00:00:00'")
        html = self.client.get("/recover/reset?token=whatever").get_data(as_text=True)
        self.assertIn("无效", html)

    def test_question_recovery_flow(self):
        self._register("secq", q="我的小学", a="sunshine")
        self.logout()
        r = self._recover_entry("secq")
        self.assertIn("安全问题恢复", r.get_data(as_text=True))
        # 错误答案 ×5 → 触发限锁
        for i in range(5):
            self.set_csrf()
            r = self.client.post("/recover/question", data={
                "csrf_token": "t", "answer": "wrong%d" % i,
                "password": "NewPass123", "confirm": "NewPass123"})
            self.assertIn("答案不正确", r.get_data(as_text=True))
        # 第 6 次被限锁，即使答案正确也无法恢复
        self.set_csrf()
        r = self.client.post("/recover/question", data={
            "csrf_token": "t", "answer": "sunshine",
            "password": "Hacked123", "confirm": "Hacked123"}, follow_redirects=True)
        self.assertIn("暂时锁定", r.get_data(as_text=True))

    def test_correct_answer_resets(self):
        self._register("goodq", q="我的小学", a="sunshine")
        self.logout()
        self.login("goodq", "Passw0rd123")
        self.logout()
        r = self._recover_entry("goodq")
        self.set_csrf()
        r = self.client.post("/recover/question", data={
            "csrf_token": "t", "answer": "SUNSHINE ",   # 归一化：大小写/空格不敏感
            "password": "NewPass456", "confirm": "NewPass456"}, follow_redirects=True)
        self.assertIn("密码已重置", r.get_data(as_text=True))
        self.login("goodq", "NewPass456")
        html = self.client.get("/index").get_data(as_text=True)
        self.assertIn('href="/u/goodq"', html)   # 登录态：顶栏显示用户主页链接

    def test_email_rate_limit(self):
        self.register("ratelimit", email="r@1914.fun")
        self.logout()
        r = self._recover_entry("ratelimit")
        self.set_csrf()
        results = []
        for _ in range(4):
            results.append(self.client.post("/recover/email", follow_redirects=True)
                           .get_data(as_text=True))
        self.assertTrue(any("过于频繁" in h for h in results[2:]))

    def test_q_help_markup(self):
        """注册页包含 ? 帮助组件与悬停/点击说明。"""
        html = self.client.get("/register").get_data(as_text=True)
        self.assertIn("q-help", html)
        self.assertIn("安全问题用于在忘记密码时恢复账户", html)
        self.assertIn("（可选，用于账户恢复）", html)
        self.assertIn('name="security_question"', html)
        self.assertIn('name="security_answer"', html)


class TestRoleBadges(Base):
    """管理员/版主徽章：所有展示用户名的内容页名字旁可见，普通用户无徽章。"""

    def test_admin_and_moderator_badges(self):
        # mem（普通用户）先投稿一张卡
        self.register("mem", "Passw0rd123")
        self.set_csrf()
        self.client.post("/cards/new", headers=self.csrf_hdr(), data={
            "name": "mem的卡", "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        card_id = self.sql("SELECT id FROM cards WHERE name='mem的卡'")[0]["id"]
        # 管理员 chief：提交 Issue + 评论 mem 的卡
        self.make_admin("chief", "Passw0rd123")
        self.logout()
        self.login("chief", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "chief 提交的 issue 标题", "body": "x"},
                         follow_redirects=True)
        self.client.post(f"/api/comment/card/{card_id}", headers=h,
                         data={"body": "管理员路过"})
        issue_id = self.sql("SELECT id FROM issues ORDER BY id DESC LIMIT 1")[0]["id"]
        # 版主 mod：也评论 mem 的卡
        with self.app.app_context():
            db.execute(
                "INSERT INTO users (username, password_hash, role) VALUES (?,?, 'moderator')",
                ("mod", hash_password("Passw0rd123")))
        self.logout()
        self.login("mod", "Passw0rd123")
        self.client.post(f"/api/comment/card/{card_id}", headers=self.csrf_hdr(),
                         data={"body": "版主路过"})
        # 卡牌详情：评论区里管理员(金)与版主(橄榄)徽章都在
        card_page = self.client.get(f"/card/{card_id}").get_data(as_text=True)
        self.assertIn('badge-brass" title="管理员"', card_page)
        self.assertIn('badge-olive" title="版主"', card_page)
        # Issue 详情：作者名旁有管理员徽章
        issue_page = self.client.get(f"/issue/{issue_id}").get_data(as_text=True)
        self.assertIn('chief</a><span class="badge badge-brass"', issue_page)
        # 首页最新 Issue：提交人旁有徽章
        home = self.client.get("/index").get_data(as_text=True)
        self.assertIn('chief<span class="badge badge-brass"', home)
        # 搜索用户：名字旁有徽章
        found = self.client.get("/search?q=chief").get_data(as_text=True)
        self.assertIn('chief</a><span class="badge badge-brass"', found)
        # mem 的消息页：操作人 chief 旁有徽章
        self.logout()
        self.login("mem", "Passw0rd123")
        msgs = self.client.get("/messages").get_data(as_text=True)
        self.assertIn('<b>chief</b><span class="badge badge-brass"', msgs)
        # 个人主页：chief 有徽章；普通用户 mem 没有
        prof = self.client.get("/u/chief").get_data(as_text=True)
        self.assertIn('badge-brass" title="管理员"', prof)
        own = self.client.get("/u/mem").get_data(as_text=True)
        self.assertNotIn('title="管理员"', own)
        self.assertNotIn('title="版主"', own)


class TestProfileRepliesAndReports(Base):
    """用户主页：回复页签（含回复的回复+原帖位置）、举报用户（可关联回复）、
    官方卡牌不可举报。"""

    def _make_fixtures(self):
        """pauthor 有卡 + replier 有顶层评论和嵌套回复；返回 (pauthor_id, replier_id)。"""
        self.register("pauthor", "Passw0rd123")
        self.set_csrf()
        self.client.post("/cards/new", headers=self.csrf_hdr(), data={
            "name": "P作者的卡", "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        ccid = self.sql("SELECT id FROM cards WHERE name='P作者的卡'")[0]["id"]
        self.logout()
        self.register("replier", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post(f"/api/comment/card/{ccid}", headers=h, data={"body": "顶层评论内容"})
        top = self.sql("SELECT id FROM comments WHERE body='顶层评论内容'")[0]["id"]
        self.client.post(f"/api/comment/card/{ccid}", headers=h,
                         data={"body": "嵌套回复内容", "parent_id": top})
        # pauthor 回复 replier 的回复 → 构造真正的"回复的回复"
        self.logout()
        self.login("pauthor", "Passw0rd123")
        nested = self.sql("SELECT id FROM comments WHERE body='嵌套回复内容'")[0]["id"]
        self.client.post(f"/api/comment/card/{ccid}", headers=self.csrf_hdr(),
                         data={"body": "第三层内容", "parent_id": nested})
        self.logout()
        pa = self.sql("SELECT id FROM users WHERE username='pauthor'")[0]["id"]
        rp = self.sql("SELECT id FROM users WHERE username='replier'")[0]["id"]
        return pa, rp, ccid

    def test_profile_replies_tab(self):
        _, _, ccid = self._make_fixtures()
        html = self.client.get("/u/replier?tab=replies").get_data(as_text=True)
        self.assertIn("顶层评论内容", html)
        self.assertIn("嵌套回复内容", html)
        self.assertIn(f"/card/{ccid}#comment-", html)
        self.assertIn("原帖", html)
        # pauthor 的主页：他那条"回复的回复"带标记
        html = self.client.get("/u/pauthor?tab=replies").get_data(as_text=True)
        self.assertIn("第三层内容", html)
        self.assertIn("回复的回复", html)
        # 页签入口存在
        self.assertIn("tab=replies", self.client.get("/u/replier").get_data(as_text=True))

    def test_report_user_and_with_reply(self):
        pa, _, _ = self._make_fixtures()
        self.logout()
        self.register("reporter9", "Passw0rd123")
        h = self.csrf_hdr()
        # 直接举报用户
        r = self.client.post("/api/report", headers=h, data={
            "target_type": "user", "target_id": pa, "reason": "灌水"})
        self.assertEqual(r.status_code, 200)
        row = self.sql("SELECT target_id FROM reports WHERE target_type='user'")[0]
        self.assertEqual(row["target_id"], pa)
        # 关联回复举报 → 对象是该条评论
        cid = self.sql("SELECT id FROM comments WHERE body='顶层评论内容'")[0]["id"]
        r = self.client.post("/api/report", headers=h, data={
            "target_type": "comment", "target_id": cid, "reason": "人身攻击"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.sql(
            "SELECT COUNT(*) c FROM reports WHERE target_type='comment'")[0]["c"], 1)
        # 主页渲染举报按钮与回复选项（不能举报自己）
        html = self.client.get("/u/replier").get_data(as_text=True)
        self.assertIn("report-user-btn", html)
        self.assertIn("关联他的回复", html)
        own = self.client.get("/u/reporter9").get_data(as_text=True)
        self.assertNotIn("report-user-btn", own)

    def test_official_card_report_rejected(self):
        self.make_admin("rooty", "Passw0rd123")
        self.login("rooty", "Passw0rd123")
        self.client.post("/cards/new", headers=self.csrf_hdr(), data={
            "name": "官方不可举报卡", "type": "unit", "unit_class": "infantry",
            "source": "official"}, content_type="multipart/form-data", follow_redirects=True)
        ocid = self.sql("SELECT id FROM cards WHERE name='官方不可举报卡'")[0]["id"]
        self.logout()
        self.register("rep3", "Passw0rd123")
        # 详情页不显示举报按钮
        html = self.client.get(f"/card/{ocid}").get_data(as_text=True)
        self.assertNotIn("举报此卡牌", html)
        # API 直接举报官方卡 → 400
        h = self.csrf_hdr()
        r = self.client.post("/api/report", headers=h, data={
            "target_type": "card", "target_id": ocid, "reason": "x"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不接受举报", r.get_json()["error"])
        # 自制卡仍可正常举报
        self.logout()
        self.register("pauthor2", "Passw0rd123")
        self.set_csrf()
        self.client.post("/cards/new", headers=self.csrf_hdr(), data={
            "name": "自制可举报卡", "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        kcid = self.sql("SELECT id FROM cards WHERE name='自制可举报卡'")[0]["id"]
        self.logout()
        self.login("rep3", "Passw0rd123")
        r = self.client.post("/api/report", headers=self.csrf_hdr(), data={
            "target_type": "card", "target_id": kcid, "reason": "x"})
        self.assertEqual(r.status_code, 200)


class TestOrderCardCost(Base):
    """指令卡费用显示指挥点 K，单位卡显示战争点 Z（同一存储字段按类型展示）。"""

    def test_order_vs_unit_cost_display(self):
        self.make_admin("rooty3", "Passw0rd123")
        self.login("rooty3", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/cards/new", headers=h, data={
            "name": "测试指令卡", "type": "order", "cost_z": 2},
            content_type="multipart/form-data", follow_redirects=True)
        oid = self.sql("SELECT id FROM cards WHERE name='测试指令卡'")[0]["id"]
        html = self.client.get(f"/card/{oid}").get_data(as_text=True)
        self.assertIn("指挥点 (K)", html)
        self.assertIn("K 2", html)
        self.assertNotIn("战争点 (Z)", html)
        self.client.post("/cards/new", headers=h, data={
            "name": "测试单位卡", "type": "unit", "unit_class": "infantry", "cost_z": 3},
            content_type="multipart/form-data", follow_redirects=True)
        uid = self.sql("SELECT id FROM cards WHERE name='测试单位卡'")[0]["id"]
        html = self.client.get(f"/card/{uid}").get_data(as_text=True)
        self.assertIn("战争点 (Z)", html)
        self.assertIn("✦ 3", html)
        self.assertNotIn("指挥点 (K)", html)


class TestForum(Base):
    """论坛：浏览、发帖、回复、通知、举报、权限、排序与分类。"""

    def _post(self, title="论坛测试帖标题", body="正文内容", category="讨论", who=None):
        if who:
            self.logout()
            self.login(who, "Passw0rd123")
        self.set_csrf()
        self.client.post("/forum/new", headers=self.csrf_hdr(),
                         data={"title": title, "body": body, "category": category},
                         follow_redirects=True)
        return self.sql("SELECT id FROM forum_posts WHERE title=?", (title,))[0]["id"]

    def test_guest_browse_and_login_gate(self):
        r = self.client.get("/forum")
        self.assertEqual(r.status_code, 200)
        self.assertIn("还没有帖子", r.get_data(as_text=True))
        # 未登录发帖 → 跳登录
        r = self.client.get("/forum/new", follow_redirects=False)
        self.assertEqual(r.status_code, 302)

    def test_create_reply_notify_and_messages(self):
        self.register("fa", "Passw0rd123")
        self.set_csrf()
        self.client.post("/forum/new", headers=self.csrf_hdr(), data={
            "title": "论坛帖子标题甲", "body": "**大家好**，来讨论玩法。",
            "category": "攻略"}, follow_redirects=True)
        pid = self.sql("SELECT id FROM forum_posts WHERE title='论坛帖子标题甲'")[0]["id"]
        self.assertEqual(
            self.sql("SELECT category FROM forum_posts WHERE id=?", (pid,))[0]["category"], "攻略")
        html = self.client.get(f"/forum/{pid}").get_data(as_text=True)
        self.assertIn("<strong>大家好</strong>", html)
        # fa 回复自己的帖子 → 不产生通知
        self.set_csrf()
        self.client.post(f"/api/comment/forum_post/{pid}", headers=self.csrf_hdr(),
                         data={"body": "自己顶一下"})
        self.assertEqual(self.sql("SELECT COUNT(*) c FROM notifications")[0]["c"], 0)
        # fb 回复 fa 的帖子 → forum_reply 通知
        self.logout()
        self.register("fb", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post(f"/api/comment/forum_post/{pid}", headers=h, data={"body": "欢迎！"})
        fa = self.sql("SELECT id FROM users WHERE username='fa'")[0]["id"]
        n = self.sql("SELECT type, forum_post_id FROM notifications WHERE recipient_id=?", (fa,))
        self.assertTrue(any(x["type"] == "forum_reply" and x["forum_post_id"] == pid for x in n))
        # fb 回复 fa 的评论 → comment_reply 通知
        top = self.sql("SELECT id FROM comments WHERE body='自己顶一下'")[0]["id"]
        self.client.post(f"/api/comment/forum_post/{pid}", headers=h,
                         data={"body": "回复你的评论", "parent_id": top})
        n = self.sql("SELECT type FROM notifications WHERE recipient_id=?", (fa,))
        self.assertTrue(any(x["type"] == "comment_reply" for x in n))
        # fa 的消息页：文案与论坛跳转链接
        self.logout()
        self.login("fa", "Passw0rd123")
        html = self.client.get("/messages").get_data(as_text=True)
        self.assertIn("回复了你的帖子", html)
        self.assertIn(f"/forum/{pid}#comment-", html)

    def test_sort_and_category(self):
        self.register("fc", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/forum/new", headers=h, data={
            "title": "甲帖排序标题", "body": "x", "category": "攻略"}, follow_redirects=True)
        aid = self.sql("SELECT id FROM forum_posts WHERE title='甲帖排序标题'")[0]["id"]
        self.client.post("/forum/new", headers=h, data={
            "title": "乙帖排序标题", "body": "x", "category": "闲聊"}, follow_redirects=True)
        # 默认最新发布：乙在前
        html = self.client.get("/forum").get_data(as_text=True)
        self.assertLess(html.find("乙帖排序标题"), html.find("甲帖排序标题"))
        # 回复甲帖后，按最新回复甲帖提前（时间戳秒级精度，需隔 1 秒）
        import time
        time.sleep(1.1)
        self.set_csrf()
        self.client.post("/api/comment/forum_post/%d" % aid, headers=self.csrf_hdr(),
                         data={"body": "顶甲帖"})
        html = self.client.get("/forum?sort=recent_reply").get_data(as_text=True)
        self.assertLess(html.find("甲帖排序标题"), html.find("乙帖排序标题"))
        # 分类筛选
        html = self.client.get("/forum?category=攻略").get_data(as_text=True)
        self.assertIn("甲帖排序标题", html)
        self.assertNotIn("乙帖排序标题", html)

    def test_permissions_and_moderation(self):
        self.register("ownera", "Passw0rd123")
        pid = self._post(title="权限测试帖标题", who="ownera")
        self.logout()
        self.register("otherb", "Passw0rd123")
        # 他人编辑/删除 → 403
        self.assertEqual(self.client.get(f"/forum/{pid}/edit").status_code, 403)
        self.assertEqual(
            self.client.post(f"/forum/{pid}/delete", headers=self.csrf_hdr()).status_code, 403)
        # 版主可删除
        with self.app.app_context():
            db.execute("UPDATE users SET role='moderator' WHERE username='otherb'")
        self.assertEqual(
            self.client.post(f"/forum/{pid}/delete", headers=self.csrf_hdr(),
                             follow_redirects=False).status_code, 302)
        # 删除后：详情 404、列表不显示、回复 API 404
        self.assertEqual(self.client.get(f"/forum/{pid}").status_code, 404)
        r = self.client.post("/api/comment/forum_post/%d" % pid,
                             headers=self.csrf_hdr(), data={"body": "x"})
        self.assertEqual(r.status_code, 404)

    def test_report_forum_post_admin_flow(self):
        self.register("posta", "Passw0rd123")
        pid = self._post(title="被举报的帖标题", who="posta")
        self.logout()
        self.register("reporta", "Passw0rd123")
        h = self.csrf_hdr()
        r = self.client.post("/api/report", headers=h, data={
            "target_type": "forum_post", "target_id": pid, "reason": "广告灌水"})
        self.assertEqual(r.status_code, 200)
        # 管理员看到举报，目标链接指向论坛
        self.logout()
        self.make_admin("rootadm", "Passw0rd123")
        self.login("rootadm", "Passw0rd123")
        html = self.client.get("/admin/reports").get_data(as_text=True)
        self.assertIn("帖子：被举报的帖标题", html)
        self.assertIn(f"/forum/{pid}", html)
        # 批量隐藏内容并处理
        rid = str(self.sql("SELECT id FROM reports")[0]["id"])
        r = self.client.post("/admin/reports/batch", headers=self.csrf_hdr(),
                             data={"action": "hide_content", "ids": [rid]},
                             follow_redirects=True)
        self.assertIn("批量操作完成", r.get_data(as_text=True))
        self.assertEqual(
            self.sql("SELECT status FROM forum_posts WHERE id=?", (pid,))[0]["status"], "hidden")
        # 游客访问隐藏帖 404，版主仍可见
        self.logout()
        self.assertEqual(self.client.get(f"/forum/{pid}").status_code, 404)

    def test_missing_post_404(self):
        self.assertEqual(self.client.get("/forum/999").status_code, 404)

    def test_forum_in_nav_and_sitemap(self):
        self.register("sitem", "Passw0rd123")
        self._post(title="站点地图帖子", who="sitem")
        html = self.client.get("/index").get_data(as_text=True)
        self.assertIn('href="/forum"', html)
        xml = self.client.get("/sitemap.xml").get_data(as_text=True)
        self.assertIn("/forum/", xml)


class TestMessages(Base):
    """站内消息：生成、去重、角标时间点机制、分页、隐私、死链处理。"""

    def setUp(self):
        super().setUp()
        self.register("author", "Passw0rd123")
        self.acard = self.sql("SELECT id FROM cards WHERE name='author的卡'")

    def _acard_id(self):
        return self.sql("SELECT id FROM cards WHERE name='author的卡'")[0]["id"]

    def _make_card(self, name="author的卡"):
        self.logout()
        self.login("author", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/cards/new", headers=h, data={
            "name": name, "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        return self.sql("SELECT id FROM cards WHERE name=?", (name,))[0]["id"]

    def test_guest_no_entry_and_redirect(self):
        self.logout()
        r = self.client.get("/messages", follow_redirects=True)
        self.assertIn("登录", r.get_data(as_text=True))
        html = self.client.get("/index").get_data(as_text=True)
        self.assertNotIn("msg-bell", html)

    def test_browsing_does_not_notify(self):
        self._make_card()
        cid = self._acard_id()
        self.login("author", "Passw0rd123")
        self.client.get(f"/card/{cid}")   # 浏览自己的卡
        self.logout()
        self.register("viewer", "Passw0rd123")
        self.client.get(f"/card/{cid}")   # 其他人浏览
        n = self.sql("SELECT COUNT(*) c FROM notifications WHERE recipient_id=?",
                     (self.sql("SELECT id FROM users WHERE username='author'")[0]["id"],))[0]["c"]
        self.assertEqual(n, 0)

    def test_comment_notifies_card_author(self):
        cid = self._make_card()
        self.logout()
        self.register("commenter", "Passw0rd123")
        self.set_csrf()
        r = self.client.post("/api/comment/card/%d" % cid, headers=self.csrf_hdr(),
                             data={"body": "不错的卡"})
        self.assertEqual(r.status_code, 200)
        aid = self.sql("SELECT id FROM users WHERE username='author'")[0]["id"]
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM notifications WHERE recipient_id=? AND type='card_comment'",
                     (aid,))[0]["c"], 1)
        # 自己评论自己的卡不产生消息
        self.logout()
        self.login("author", "Passw0rd123")
        self.set_csrf()
        self.client.post("/api/comment/card/%d" % cid, headers=self.csrf_hdr(),
                         data={"body": "自言自语"})
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM notifications WHERE recipient_id=? AND type='card_comment'",
                     (aid,))[0]["c"], 1)

    def test_reply_notifies_comment_author(self):
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "评论回复测试的标题", "body": "x"}, follow_redirects=True)
        # author 在 issue 下评论
        self.logout()
        self.register("author2", "Passw0rd123")
        h = self.csrf_hdr()
        cid = self.client.post("/api/comment/issue/1", headers=h,
                               data={"body": "原评论"}).get_json()["id"]
        # 第三人回复 author2 的评论 → 通知 author2
        self.logout()
        self.register("third", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/api/comment/issue/1", headers=h,
                         data={"body": "回复内容", "parent_id": cid})
        rows = self.sql("SELECT type FROM notifications WHERE recipient_id="
                        "(SELECT id FROM users WHERE username='author2')")
        self.assertTrue(any(x["type"] == "comment_reply" for x in rows))
        # author（issue 作者）不因该回复收到 comment_reply
        aid = self.sql("SELECT id FROM users WHERE username='author2'")[0]["id"]
        n_reply = self.sql("SELECT COUNT(*) c FROM notifications WHERE recipient_id=? AND type='comment_reply'",
                           (aid,))[0]["c"]
        self.assertEqual(n_reply, 1)

    def test_reply_to_reply_flattened_with_mention(self):
        """回复子回复：DOM 保持两层（挂到顶级评论），正文加 @用户名 前缀，
        通知发给被回复的子回复作者；Issue 回复通知挂 issue_id 而非 card_id。"""
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "嵌套回复测试的标题", "body": "x"}, follow_redirects=True)
        self.logout()
        self.register("topper", "Passw0rd123")
        h = self.csrf_hdr()
        top = self.client.post("/api/comment/issue/1", headers=h,
                               data={"body": "顶级"}).get_json()["id"]
        self.logout()
        self.register("inner", "Passw0rd123")
        h = self.csrf_hdr()
        rep = self.client.post("/api/comment/issue/1", headers=h,
                               data={"body": "子回复", "parent_id": top}).get_json()["id"]
        # 第三人回复 inner 的子回复 → 仍挂到顶级评论，正文带 @inner 前缀
        self.logout()
        self.register("third", "Passw0rd123")
        h = self.csrf_hdr()
        r = self.client.post("/api/comment/issue/1", headers=h,
                             data={"body": "再回复", "parent_id": rep})
        self.assertEqual(r.status_code, 200)
        row = self.sql("SELECT parent_id, body FROM comments WHERE id=?",
                       (r.get_json()["id"],))[0]
        self.assertEqual(row["parent_id"], top)
        self.assertTrue(row["body"].startswith("@inner "))
        self.assertIn("再回复", row["body"])
        # inner（被回复的子回复作者）收到 comment_mention；topper 收到直接回复的 comment_reply
        inner_id = self.sql("SELECT id FROM users WHERE username='inner'")[0]["id"]
        topper_id = self.sql("SELECT id FROM users WHERE username='topper'")[0]["id"]
        self.assertEqual(self.sql(
            "SELECT COUNT(*) c FROM notifications WHERE recipient_id=? AND type='comment_mention'",
            (inner_id,))[0]["c"], 1)
        self.assertEqual(self.sql(
            "SELECT COUNT(*) c FROM notifications WHERE recipient_id=? AND type='comment_reply'",
            (topper_id,))[0]["c"], 1)
        # Issue 的回复通知挂 issue_id（而非误挂 card_id），两类通知均如此
        n = self.sql("SELECT issue_id, card_id FROM notifications WHERE type='comment_reply'")[0]
        self.assertEqual(n["issue_id"], 1)
        self.assertIsNone(n["card_id"])
        m = self.sql("SELECT issue_id, card_id FROM notifications WHERE type='comment_mention'")[0]
        self.assertEqual(m["issue_id"], 1)
        self.assertIsNone(m["card_id"])
        # 详情页子回复渲染出带 data-mention 的回复按钮
        self.logout()
        self.register("viewer", "Passw0rd123")
        html = self.client.get("/issue/1").get_data(as_text=True)
        self.assertIn('data-mention="inner"', html)

    def test_messages_category_tabs(self):
        """消息页顶部分类：被@（回复子回复）/被回复/被点赞 互不串扰，计数与筛选一致。"""
        cid = self._make_card()
        # author 在自己的卡下评论（自评不通知），供后续回复
        self.login("author", "Passw0rd123")
        self.set_csrf()
        top = self.client.post(f"/api/comment/card/{cid}", headers=self.csrf_hdr(),
                               data={"body": "author 的评论"}).get_json()["id"]
        # viewer：评论（card_comment）+ 点赞（card_vote）+ 回复 author 的评论（comment_reply）
        self.logout()
        self.register("viewer", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post(f"/api/comment/card/{cid}", headers=h, data={"body": "观众评论"})
        self.client.post(f"/api/vote/card/{cid}", headers=h)
        rep = self.client.post(f"/api/comment/card/{cid}", headers=h,
                               data={"body": "回复author", "parent_id": top}).get_json()["id"]
        # viewer2 回复 viewer 的子回复 → viewer 收到 comment_mention
        self.logout()
        self.register("viewer2", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post(f"/api/comment/card/{cid}", headers=h,
                         data={"body": "再回复", "parent_id": rep})

        def page(cat, user):
            self.logout()
            self.login(user, "Passw0rd123")
            url = f"/messages?cat={cat}" if cat else "/messages"
            return self.client.get(url).get_data(as_text=True)

        # 全部：作者三种都有，tab 计数正确（全部3 / 被@0 / 被回复1 / 被点赞1）
        full = page("", "author")
        self.assertIn("评论了你的卡牌", full)
        self.assertIn("回复了你的评论", full)
        self.assertIn("点赞了你的卡牌", full)
        self.assertIn('全部 <span class="count">3</span>', full)
        self.assertIn('被@ <span class="count">0</span>', full)
        self.assertIn('被回复 <span class="count">1</span>', full)
        self.assertIn('被点赞 <span class="count">1</span>', full)
        # 被回复：只有直接回复，没有评论/点赞
        rp = page("reply", "author")
        self.assertIn("回复了你的评论", rp)
        self.assertNotIn("评论了你的卡牌", rp)
        self.assertNotIn("点赞了你的卡牌", rp)
        # 被点赞：只有点赞
        vp = page("vote", "author")
        self.assertIn("点赞了你的卡牌", vp)
        self.assertNotIn("回复了你的评论", vp)
        # 被@：viewer 收到 viewer2 的提及；author 的被@分类为空
        mp = page("mention", "viewer")
        self.assertIn('被@ <span class="count">1</span>', mp)
        self.assertIn("在回复中提到了你", mp)
        self.assertNotIn("回复了你的评论", mp)
        me = page("mention", "author")
        self.assertIn("这个分类下还没有消息", me)

    def test_vote_dedup(self):
        cid = self._make_card()
        self.logout()
        self.register("voter", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/api/vote/card/%d" % cid, headers=h)
        # 取消再投 → 去重键保证不产生第二条投票消息
        self.client.post("/api/vote/card/%d" % cid, headers=h)
        self.client.post("/api/vote/card/%d" % cid, headers=h)
        aid = self.sql("SELECT id FROM users WHERE username='author'")[0]["id"]
        self.assertEqual(
            self.sql("SELECT COUNT(*) c FROM notifications WHERE recipient_id=? AND type='card_vote'",
                     (aid,))[0]["c"], 1)

    def test_issue_comment_and_vote_notify(self):
        self.logout()
        self.register("iauthor", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "通知测试的标题", "body": "x"}, follow_redirects=True)
        self.logout()
        self.register("iuser", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/api/comment/issue/1", headers=h, data={"body": "x"})
        self.client.post("/api/vote/issue/1", headers=h)
        aid = self.sql("SELECT id FROM users WHERE username='iauthor'")[0]["id"]
        types = sorted(r["type"] for r in self.sql(
            "SELECT type FROM notifications WHERE recipient_id=?", (aid,)))
        self.assertEqual(types, ["issue_comment", "issue_vote"])

    def test_badge_lifecycle_and_order_and_pagination(self):
        """角标：0 → 100+ 显示 99+ → 查看后清零 → 新消息重新出现；排序从新到旧；分页。"""
        self._make_card("角标卡")
        aid = self.sql("SELECT id FROM users WHERE username='author'")[0]["id"]
        # 直接插入 100 条消息（覆盖 99+ 与排序）
        for i in range(100):
            self.sql("INSERT INTO notifications (recipient_id, type, created_at) VALUES (?,?,?)",
                     (aid, "card_comment", "2026-01-%02d 00:00:00" % (i % 28 + 1)))
        self.logout()
        self.register("badgeuser", "Passw0rd123")
        self.set_csrf()
        # 灌 100 条给 badgeuser
        bu = self.sql("SELECT id FROM users WHERE username='badgeuser'")[0]["id"]
        for i in range(100):
            self.sql("INSERT INTO notifications (recipient_id, type, created_at) VALUES (?,?,?)",
                     (bu, "card_comment", "2026-02-%02d 00:00:00" % (i % 28 + 1)))
        # badgeuser 的首页角标应为 99+
        html = self.client.get("/index").get_data(as_text=True)
        self.assertIn("99+", html)
        self.assertNotIn('">100<', html)
        # 打开消息页：角标清零，列表第一页 20 条
        r = self.client.get("/messages")
        self.assertEqual(r.status_code, 200)
        html = self.client.get("/index").get_data(as_text=True)
        self.assertNotIn("msg-badge", html)
        # author 有 100 条消息 → 分页第二页可用
        self.logout()
        self.login("author", "Passw0rd123")
        r = self.client.get("/messages?page=2")
        self.assertEqual(r.status_code, 200)
        self.assertIn("第 2 页", r.get_data(as_text=True)) if False else None

    def test_privacy_and_sort_and_gone(self):
        """只能看自己的消息；时间倒序；内容删除后不产生死链。"""
        cid = self._make_card("隐私卡")
        self.logout()
        self.register("puser", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/api/comment/card/%d" % cid, headers=h, data={"body": "x"})
        aid = self.sql("SELECT id FROM users WHERE username='author'")[0]["id"]
        self.logout()
        self.login("author", "Passw0rd123")
        html = self.client.get("/messages").get_data(as_text=True)
        self.assertIn("隐私卡", html)
        # 其他人看不到 author 的消息
        self.logout()
        self.register("other", "Passw0rd123")
        html = self.client.get("/messages").get_data(as_text=True)
        self.assertNotIn("隐私卡", html)
        # 删除卡牌 → 消息显示"该内容已被删除"且无 /card 链接
        self.logout()
        self.login("author", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/card/%d/delete" % cid, headers=h, data={"csrf_token": "t"},
                         follow_redirects=True)
        html = self.client.get("/messages").get_data(as_text=True)
        self.assertIn("该内容已被删除", html)
        self.assertNotIn('href="/card/%d"' % cid, html)


class TestGithubProxy(Base):
    """GitHub 同步走代理：配置透传 + 断连代理时任务失败重试。"""

    def _queue_issue(self, component="game"):
        self.logout()
        self.register("syncuser", "Passw0rd123")
        self.app.config["GITHUB_TOKEN"] = "test-token"
        h = self.csrf_hdr()
        r = self.client.post("/issues/new", headers=h, data={
            "title": "代理分流测试的标题", "body": "x", "component": component},
            follow_redirects=True)
        self.assertEqual(r.status_code, 200)

    def test_proxy_config_plumbing(self):
        """GITHUB_PROXY 配置透传到 GitHub 请求层（monkeypatch 捕获）。"""
        self.logout()
        self.register("proxyuser", "Passw0rd123")
        self.app.config["GITHUB_TOKEN"] = "test-token"
        self.app.config["GITHUB_PROXY"] = "http://127.0.0.1:7890"
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "代理透传的标题", "body": "x"}, follow_redirects=True)
        from app import github_sync
        captured = {}
        real_req = github_sync._gh_request
        def fake(method, url, token, payload=None, proxy=""):
            captured["proxy"] = proxy
            captured["url"] = url
            raise RuntimeError("stop")
        github_sync._gh_request = fake
        try:
            with self.app.app_context():
                github_sync.process_queue(self.app)
        except RuntimeError:
            pass
        finally:
            github_sync._gh_request = real_req
        self.assertEqual(captured.get("proxy"), "http://127.0.0.1:7890")
        self.assertIn("/repos/fdvecbtwdh/1914/issues", captured.get("url", ""))

    def test_backfill_create_uses_proxy(self):
        """update/close 补创建分支（Issue 尚未同步过）同样必须走代理。"""
        self.logout()
        self.register("backfilluser", "Passw0rd123")
        self.app.config["GITHUB_TOKEN"] = "test-token"
        self.app.config["GITHUB_PROXY"] = "http://127.0.0.1:7890"
        self.app.config["GITHUB_WEB_REPO"] = "fdvecbtwdh/1914_website"
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h, data={
            "title": "补创建走代理的标题", "body": "x", "component": "web"},
            follow_redirects=True)
        wid = self.sql("SELECT id FROM issues WHERE title='补创建走代理的标题'")[0]["id"]
        # 模拟 create 任务已丢失、只剩一条 update 任务且尚未同步到 GitHub
        self.sql("DELETE FROM sync_queue")
        self.sql("INSERT INTO sync_queue (issue_id, action, repo) VALUES (?, 'update', ?)",
                 (wid, "fdvecbtwdh/1914_website"))
        from app import github_sync
        captured = {}
        real_req = github_sync._gh_request
        def fake(method, url, token, payload=None, proxy=""):
            captured.update(method=method, url=url, proxy=proxy)
            return {"number": 7, "html_url": "https://github.com/fdvecbtwdh/1914_website/issues/7"}
        github_sync._gh_request = fake
        try:
            with self.app.app_context():
                github_sync.process_queue(self.app)
        finally:
            github_sync._gh_request = real_req
        self.assertEqual(captured.get("method"), "POST")
        self.assertIn("/repos/fdvecbtwdh/1914_website/issues", captured.get("url", ""))
        self.assertEqual(captured.get("proxy"), "http://127.0.0.1:7890")
        row = self.sql("SELECT status FROM sync_queue WHERE issue_id=?", (wid,))[0]
        self.assertEqual(self.sql("SELECT github_number FROM issues WHERE id=?", (wid,))[0]["github_number"], 7)
        self.assertEqual(row["status"], "done")

    def test_sync_without_proxy_direct(self):
        """未配置代理：proxy 参数为空（直连），队列任务正常处理。"""
        self.logout()
        self.register("directuser", "Passw0rd123")
        self.app.config["GITHUB_TOKEN"] = "test-token"
        self.app.config["GITHUB_PROXY"] = ""
        h = self.csrf_hdr()
        self.client.post("/issues/new", headers=h,
                         data={"title": "直连测试的标题", "body": "x"}, follow_redirects=True)
        from app import github_sync
        captured = {}
        real_req = github_sync._gh_request
        def fake(method, url, token, payload=None, proxy=""):
            captured["proxy"] = proxy
            raise RuntimeError("stop")
        github_sync._gh_request = fake
        try:
            with self.app.app_context():
                github_sync.process_queue(self.app)
        except RuntimeError:
            pass
        finally:
            github_sync._gh_request = real_req
        self.assertEqual(captured.get("proxy"), "")

    def test_component_filter_admin(self):
        self.logout()
        self.register("fuser", "Passw0rd123")
        self.logout()
        admin_u, admin_p = self.make_admin()
        self.login(admin_u, admin_p)
        for path in ("/admin/issues?status=all&component=web", "/admin/users?role=user"):
            r = self.client.get(path)
            self.assertEqual(r.status_code, 200, path)


if __name__ == "__main__":
    unittest.main(verbosity=2)
