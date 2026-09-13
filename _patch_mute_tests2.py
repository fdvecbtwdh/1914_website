import io

p = "tests/test_site.py"
s = io.open(p, encoding="utf-8").read()
anchor = "class TestMessages(Base):"
tests = '''class TestMuteBan(Base):
    """禁言（后端强制、到期自动解除、通知、权限）与封禁原因。"""

    def _admin_login(self):
        self.make_admin("mroot", "Passw0rd123")
        self.logout()
        self.login("mroot", "Passw0rd123")

    def _uid(self, name):
        return self.sql("SELECT id FROM users WHERE username=?", (name,))[0]["id"]

    def _mute(self, uid, reason="恶意刷屏", **kw):
        data = {"reason": reason}
        data.update(kw)
        return self.client.post("/admin/users/%d/mute" % uid,
                                headers=self.csrf_hdr(), data=data,
                                follow_redirects=False)

    def test_mute_12h_blocks_everything(self):
        self.register("muted2", "Passw0rd123")
        uid = self._uid("muted2")
        self._admin_login()
        r = self._mute(uid, duration_value=12, duration_unit="hour")
        self.assertEqual(r.status_code, 302)
        row = self.sql("SELECT mute_until, mute_reason FROM users WHERE id=?", (uid,))[0]
        self.assertIn("恶意刷屏", row["mute_reason"])
        self.assertNotEqual(row["mute_until"], "permanent")
        self.assertEqual(self.sql(
            "SELECT COUNT(*) c FROM penalties WHERE user_id=? AND type='mute'",
            (uid,))[0]["c"], 1)
        self.assertTrue(any(x["type"] == "user_mute" for x in
            self.sql("SELECT type FROM notifications WHERE recipient_id=?", (uid,))))
        # 被禁言者：评论 API 403 且带原因；论坛/Issue/卡牌提交被拦
        self.logout()
        self.login("muted2", "Passw0rd123")
        h = self.csrf_hdr()
        r = self.client.post("/api/comment/card/1", headers=h, data={"body": "x"})
        self.assertEqual(r.status_code, 403)
        body = r.get_json()["error"]
        self.assertIn("禁言", body)
        self.assertIn("恶意刷屏", body)
        r = self.client.post("/forum/new", headers=h, data={"title": "abc", "body": "x"},
                             follow_redirects=False)
        self.assertEqual(r.status_code, 403)
        self.assertEqual(self.sql("SELECT COUNT(*) c FROM forum_posts")[0]["c"], 0)
        self.client.post("/issues/new", headers=h, data={"title": "标题足够长", "body": "x"},
                         follow_redirects=True)
        self.assertEqual(self.sql("SELECT COUNT(*) c FROM issues")[0]["c"], 0)
        self.client.post("/cards/new", headers=h, data={
            "name": "禁言期间的卡", "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        self.assertEqual(self.sql(
            "SELECT COUNT(*) c FROM cards WHERE name='禁言期间的卡'")[0]["c"], 0)
        # 浏览不受影响
        self.assertEqual(self.client.get("/forum").status_code, 200)
        self.assertEqual(self.client.get("/u/muted2").status_code, 200)

    def test_mute_expiry_auto_restore(self):
        self.register("muted3", "Passw0rd123")
        uid = self._uid("muted3")
        self._admin_login()
        self.client.post("/cards/new", headers=self.csrf_hdr(), data={
            "name": "目标卡", "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        cid = self.sql("SELECT id FROM cards WHERE name='目标卡'")[0]["id"]
        self._mute(uid, duration_value=1, duration_unit="hour")
        # 模拟到期：把解禁时间改到过去（服务器时间判断，无需定时任务）
        self.sql("UPDATE users SET mute_until='2000-01-01 00:00:00' WHERE id=?", (uid,))
        self.logout()
        self.login("muted3", "Passw0rd123")
        r = self.client.post("/api/comment/card/%d" % cid, headers=self.csrf_hdr(),
                             data={"body": "解禁后可以发言"})
        self.assertEqual(r.status_code, 200)

    def test_permanent_mute_and_remute_override(self):
        self.register("muted4", "Passw0rd123")
        uid = self._uid("muted4")
        self._admin_login()
        self._mute(uid, duration_value=1, duration_unit="hour")
        # 重新禁言 7 天：覆盖旧禁言
        self._mute(uid, reason="再次违规", duration_value=7, duration_unit="day")
        row = self.sql("SELECT mute_until, mute_reason FROM users WHERE id=?", (uid,))[0]
        self.assertNotIn("permanent", row["mute_until"])
        self.assertEqual(row["mute_reason"], "再次违规")
        self.assertEqual(self.sql(
            "SELECT COUNT(*) c FROM penalties WHERE user_id=? AND type='mute'",
            (uid,))[0]["c"], 2)
        self.assertEqual(self.sql(
            "SELECT COUNT(*) c FROM penalties WHERE user_id=? AND type='mute' "
            "AND lifted_at IS NOT NULL", (uid,))[0]["c"], 1)
        # 永久禁言
        self._mute(uid, reason="严重违规", permanent="1")
        row = self.sql("SELECT mute_until FROM users WHERE id=?", (uid,))[0]
        self.assertEqual(row["mute_until"], "permanent")
        self.logout()
        self.login("muted4", "Passw0rd123")
        r = self.client.post("/api/comment/card/1", headers=self.csrf_hdr(),
                             data={"body": "x"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("永久", r.get_json()["error"])

    def test_unmute_restores_immediately(self):
        self.register("muted5", "Passw0rd123")
        uid = self._uid("muted5")
        self._admin_login()
        self.client.post("/cards/new", headers=self.csrf_hdr(), data={
            "name": "解禁目标卡", "type": "unit", "unit_class": "infantry"},
            content_type="multipart/form-data", follow_redirects=True)
        cid = self.sql("SELECT id FROM cards WHERE name='解禁目标卡'")[0]["id"]
        self._mute(uid, duration_value=3, duration_unit="day")
        self.logout()
        self.login("muted5", "Passw0rd123")
        self.assertEqual(self.client.post("/api/comment/card/%d" % cid,
                                          headers=self.csrf_hdr(),
                                          data={"body": "x"}).status_code, 403)
        # 管理员解除禁言
        self._admin_login()
        r = self.client.post("/admin/users/%d/unmute" % uid, headers=self.csrf_hdr())
        self.assertEqual(r.status_code, 302)
        row = self.sql("SELECT mute_until, mute_reason FROM users WHERE id=?", (uid,))[0]
        self.assertIsNone(row["mute_until"])
        self.assertTrue(any(x["type"] == "user_unmute" for x in
            self.sql("SELECT type FROM notifications WHERE recipient_id=?", (uid,))))
        self.logout()
        self.login("muted5", "Passw0rd123")
        self.assertEqual(self.client.post("/api/comment/card/%d" % cid,
                                          headers=self.csrf_hdr(),
                                          data={"body": "解禁后发言"}).status_code, 200)

    def test_non_admin_cannot_mute_or_unmute(self):
        self.register("normalu", "Passw0rd123")
        self.register("victim", "Passw0rd123")
        vid = self._uid("victim")
        self.logout()
        self.login("normalu", "Passw0rd123")
        r = self.client.post("/admin/users/%d/mute" % vid, headers=self.csrf_hdr(),
                             data={"reason": "x", "duration_value": 1, "duration_unit": "hour"})
        self.assertEqual(r.status_code, 403)
        r = self.client.post("/admin/users/%d/unmute" % vid, headers=self.csrf_hdr())
        self.assertEqual(r.status_code, 403)
        self.assertIsNone(self.sql(
            "SELECT mute_until FROM users WHERE id=?", (vid,))[0]["mute_until"])

    def test_ban_requires_reason_and_notice(self):
        self.register("banned9", "Passw0rd123")
        uid = self._uid("banned9")
        self._admin_login()
        # 不带原因 → 拒绝
        r = self.client.post("/admin/users/%d/action" % uid, headers=self.csrf_hdr(),
                             data={"action": "ban"})
        self.assertEqual(r.status_code, 302)
        self.assertEqual(self.sql(
            "SELECT is_banned FROM users WHERE id=?", (uid,))[0]["is_banned"], 0)
        # 带原因永久封禁 → 登录页显示原因
        self.client.post("/admin/users/%d/action" % uid, headers=self.csrf_hdr(),
                         data={"action": "ban", "reason": "严重违反社区规则",
                               "permanent": "1"})
        self.assertEqual(self.sql(
            "SELECT banned_reason FROM users WHERE id=?", (uid,))[0]["banned_reason"],
            "严重违反社区规则")
        self.logout()
        r = self.client.post("/login", data={
            "username": "banned9", "password": "Passw0rd123"})
        self.assertEqual(r.status_code, 403)
        self.assertIn("严重违反社区规则", r.get_data(as_text=True))

    def test_temp_ban_expiry_auto_unban_on_login(self):
        self.register("tempban", "Passw0rd123")
        uid = self._uid("tempban")
        self._admin_login()
        self.client.post("/admin/users/%d/action" % uid, headers=self.csrf_hdr(),
                         data={"action": "ban", "reason": "短期违规",
                               "duration_value": 7, "duration_unit": "day"})
        row = self.sql("SELECT ban_until, banned_reason FROM users WHERE id=?", (uid,))[0]
        self.assertIn("短期违规", row["banned_reason"])
        self.assertIsNotNone(row["ban_until"])
        # 到期：登录时自动解封
        self.sql("UPDATE users SET ban_until='2000-01-01 00:00:00' WHERE id=?", (uid,))
        r = self.client.post("/login", data={
            "username": "tempban", "password": "Passw0rd123"}, follow_redirects=True)
        self.assertEqual(self.sql(
            "SELECT is_banned FROM users WHERE id=?", (uid,))[0]["is_banned"], 0)

    def test_admin_users_page_shows_mute_and_ban_status(self):
        self.register("shown", "Passw0rd123")
        uid = self._uid("shown")
        self._admin_login()
        self._mute(uid, reason="页面展示检查")
        html = self.client.get("/admin/users").get_data(as_text=True)
        self.assertIn("禁言中", html)
        self.assertIn("页面展示检查", html)
        self.assertIn("解除禁言", html)

    def test_report_page_mute_author_button(self):
        self.register("fa2", "Passw0rd123")
        self.set_csrf()
        self.client.post("/forum/new", headers=self.csrf_hdr(), data={
            "title": "会被举报的帖子标题", "body": "x"}, follow_redirects=True)
        pid = self.sql("SELECT id FROM forum_posts WHERE title='会被举报的帖子标题'")[0]["id"]
        self.logout()
        self.register("rep9", "Passw0rd123")
        h = self.csrf_hdr()
        self.client.post("/api/report", headers=h, data={
            "target_type": "forum_post", "target_id": pid, "reason": "灌水"})
        self._admin_login()
        html = self.client.get("/admin/reports").get_data(as_text=True)
        self.assertIn("禁言作者", html)
        self.assertIn("mute-dialog-btn", html)


class TestMessages(Base):'''
assert anchor in s, "anchor"
s = s.replace(anchor, tests, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("TestMuteBan added")
