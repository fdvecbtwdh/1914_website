import io

p = "tests/test_site.py"
s = io.open(p, encoding="utf-8").read()

# 1) 存量：test_ban_user 带原因
old1 = '''        h = self.csrf_hdr()
        self.client.post(f"/admin/users/{uid}/action", headers=h, data={"action": "ban"})
        self.assertEqual(self.sql("SELECT is_banned FROM users WHERE id=?", (uid,))[0]["is_banned"], 1)'''
new1 = '''        h = self.csrf_hdr()
        self.client.post(f"/admin/users/{uid}/action", headers=h,
                         data={"action": "ban", "reason": "测试封禁"})
        self.assertEqual(self.sql("SELECT is_banned FROM users WHERE id=?", (uid,))[0]["is_banned"], 1)
        self.assertEqual(
            self.sql("SELECT banned_reason FROM users WHERE id=?", (uid,))[0]["banned_reason"],
            "测试封禁")'''
assert old1 in s, "test_ban_user"
s = s.replace(old1, new1, 1)

# 2) 存量：批量封禁带原因
old2 = '''        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "ban", "ids": [str(uid1), str(uid2)]},
                             follow_redirects=True)'''
new2 = '''        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "ban", "ids": [str(uid1), str(uid2)],
                                   "batch_reason": "批量封禁测试"},
                             follow_redirects=True)'''
assert old2 in s, "batch ban"
s = s.replace(old2, new2, 1)

old3 = '''        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "ban", "ids": [str(me)]}, follow_redirects=True)'''
new3 = '''        r = self.client.post("/admin/users/batch", headers=h,
                             data={"action": "ban", "ids": [str(me)],
                                   "batch_reason": "x"}, follow_redirects=True)'''
assert old3 in s, "batch ban 2"
s = s.replace(old3, new3, 1)

# 3) api.py 封禁 API 消息带原因
p = "app/api.py"
s = io.open(p, encoding="utf-8").read()
old4 = '''    if u["is_banned"]:
        abort(403, description="账号已被封禁")'''
new4 = '''    if u["is_banned"]:
        abort(403, description="账号已被封禁" +
              (f"（原因：{u['banned_reason']}）" if u["banned_reason"] else ""))'''
assert old4 in s, "api ban msg"
s = s.replace(old4, new4, 1)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("legacy tests + api updated")
