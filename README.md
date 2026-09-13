# 1914.fun — 官方网站 + 卡牌社区 + Bug/Issue 平台

为 WW1 卡牌对战游戏 **1914**（Godot 4.7，仓库 `fdvecbtwdh/1914`）构建的官方网站。

```
用户 → https://1914.fun → Cloudflare → Cloudflare Tunnel → Windows 本地服务器(Flask/waitress) → SQLite
```

## 功能总览

| 模块 | 说明 |
|------|------|
| 游客浏览 | 首页 / 游戏介绍 / 卡牌列表·详情·搜索 / Issue 浏览·搜索 / 用户主页 / 评论与投票数 |
| 账号系统 | 注册（用户名+密码，邮箱选填）、Argon2 密码哈希、数据库会话（HttpOnly/Secure/SameSite cookie）、CSRF 防护、登录限速防爆破、角色 user/moderator/admin |
| 卡牌社区 | 官方卡牌从游戏项目 JSON 自动导入；玩家投稿、编辑、隐藏/删除；最新/热门/评论最多排序；按类型/兵种/稀有度/标签筛选；搜索 |
| 卡牌预览 | 网站卡面完全使用游戏真实字段（名称/兵种/经济 G/战争点 Z/攻防/视野/射程/词条/稀有度/风味文字），铜银金边框 |
| 站内消息 | 顶栏消息按钮 + 未读角标（时间点机制，无逐条已读）；收件箱按 全部/被@/被回复/被点赞 分类筛选；覆盖评论/回复/@提及/点赞互动 |
| 社区论坛 | 帖子（标题/分类/Markdown 正文）+ 回复（复用评论系统，支持回复的回复与 @ 提及）；分类筛选、最新发布/最新回复排序、分页；举报与站内消息全量接入；顶部导航/首页/移动端抽屉均可进入 |
| 账户恢复 | 邮箱（SMTP）+ 安全问题双通道；注册时可选填写，令牌一次性带有效期 |
| Issue 增强 | 所属（游戏本体/网页）+ 预设标签勾选；同步按所属分流到 1914 / 1914_website |
| 卡牌性质 | 官方卡分 正式/测试，按游戏版本自动判定：v0.* 一律测试卡，v1.0 起才是正式卡（ID 含 test/名称含 测试 的显式测试卡始终为测试）|
| Bug/Issue | 类 GitHub Issues：Open / In Progress / Resolved / Closed / Duplicate；优先级、标签、Markdown（消毒后渲染）、复现步骤 |
| 评论 | 两层评论（评论+回复；回复子回复自动加 @用户名 提及并通知对方）、编辑、删除、举报、Markdown 消毒 |
| 投票 | 卡牌/Issue/评论，(user, target) 唯一约束，后端校验，点击切换 |
| 举报 | 举报卡牌/Issue/评论/用户，管理员处理/忽略/隐藏 |
| 管理后台 | `/admin`：用户（封禁/角色）、卡牌（隐藏/删除/导入）、Issue、评论、举报、标签、GitHub 同步队列、审计日志 |
| GitHub 同步 | 可选。Issue 变更入队，后台线程推送 GitHub；Token 未配置/网络失败时站点完全不受影响 |
| SEO | Title/Description、Open Graph、canonical、favicon、sitemap.xml、robots.txt、卡牌与 Issue 独立 URL |

## 快速开始（Windows）

```powershell
# 0) 要求 Python 3.11+（开发环境为 3.14）
cd D:\Code\1914_website

# 1) 安装依赖
powershell -ExecutionPolicy Bypass -File scripts\install.ps1

# 2) 初始化（自动生成 .env、建库、导入游戏卡牌、建管理员）
powershell -ExecutionPolicy Bypass -File scripts\init.ps1

# 3) 启动
powershell -ExecutionPolicy Bypass -File scripts\start-prod.ps1   # 生产 (waitress, 0.0.0.0:8000)
powershell -ExecutionPolicy Bypass -File scripts\start-dev.ps1    # 开发 (调试模式, 127.0.0.1:8000)
```

浏览器打开 `http://127.0.0.1:8000` 验证，然后对接 Cloudflare Tunnel（见下）。

## Cloudflare Tunnel 对接

前提：`cloudflared` 已登录且域名 `1914.fun` 托管在 Cloudflare（已有 Tunnel 的话直接看第 3 步）。

```powershell
# 1) 安装 cloudflared（winget 或官网下载 exe）
winget install Cloudflare.cloudflared

# 2) 登录并创建隧道
cloudflared tunnel login
cloudflared tunnel create 1914fun

# 3) 配置路由：把 1914.fun 指到本地 8000 端口
cloudflared tunnel route dns 1914fun 1914.fun
# 也可在 Cloudflare 后台 Public Hostname 里配置：1914.fun → http://localhost:8000

# 4) 运行（config.yml 示例见下）
cloudflared tunnel run 1914fun
```

`%USERPROFILE%\.cloudflared\config.yml`：

```yaml
tunnel: 1914fun
credentials-file: C:\Users\<你>\.cloudflared\<隧道UUID>.json
ingress:
  - hostname: 1914.fun
    service: http://localhost:8000
  - hostname: www.1914.fun
    service: http://localhost:8000
  - service: http_status:404
```

### 开机自启（推荐）

把隧道与站点都注册为 Windows 服务，重启后自动恢复：

```powershell
# 站点服务（用 NSSM 更省事，或用任务计划程序）
winget install nssm
nssm install 1914site "D:\Code\1914_website\.venv\Scripts\python.exe" "D:\Code\1914_website\wsgi.py"
nssm set 1914site AppDirectory D:\Code\1914_website
nssm set 1914site AppEnvironmentExtra PORT=8000
nssm start 1914site

# 隧道服务（cloudflared 自带）
cloudflared service install
```

## 游戏卡牌数据同步

游戏项目里的卡牌是唯一官方数据源（`D:\Code\1914\data\cards\**\*.json`）。
游戏内改了卡牌数值后，任选其一同步到网站：

- 后台：管理员登录 → `/admin/cards` → 「从游戏项目导入官方卡牌」
- 命令行：`.venv\Scripts\python manage.py import-cards`
- 游戏项目路径不可用时自动回退到仓库内置 `seed/cards/` 副本

卡牌字段映射（与游戏 JSON 一一对应，见 `app/gameconstants.py`）：

```
id → game_id    name → 名称    type → unit/order    unit_class → 兵种
cost_g → 经济 G  cost_z → 战争点 Z（部署）  attack/defense → 攻/防
vision_range/attack_range → 视野/射程（枚举文案同 game-mechanics.md）
abilities → 词条（支持等级如 "坚守2"）  rarity → common/silver/gold（铜/银/金）
art → 卡面图    flavor_text → 风味文字
```

## GitHub Issue 同步（可选）

1. GitHub → Settings → Developer settings → Personal access tokens (classic) → 勾选 `repo` 权限
2. 写入 `.env`：`GITHUB_TOKEN=ghp_xxx`（`GITHUB_REPO` 默认 `fdvecbtwdh/1914`）
3. 重启站点。之后每次创建/编辑/关闭 Issue 会自动推送到 GitHub（队列化后台执行）

设计原则：**网站数据库是主数据源**。GitHub 不可达/Token 失效时任务保留重试（最多 5 次），
状态可在 `/admin/github` 查看，站点任何功能都不会因此不可用。

## 测试

```powershell
.venv\Scripts\python -m unittest tests.test_site -v   # 87 项断言：账号/安全/投票/评论/Issue/导入/后台/消息/恢复
```

## 目录结构

```
app/
├── __init__.py        应用工厂（安全响应头/节流/错误页/上下文注入）
├── config.py          配置（.env 加载）
├── db.py              SQLite 访问层（参数化查询，SQL 标准，便于迁移 PG）
├── schema.sql         表结构（users/sessions/cards/issues/comments/votes/labels/
│                      reports/notifications/sync_queue/audit_log/rate_limits/settings）
├── auth.py            注册/登录/会话/CSRF/限速/角色
├── cards.py           卡牌社区（列表/详情/投稿/编辑）
├── card_sync.py       游戏卡牌导入
├── issues.py          Issue 系统（状态/优先级/标签）
├── interactions.py    投票/评论/举报（卡牌与 Issue 共用）
├── github_sync.py     GitHub 同步队列 + 后台线程（stdlib urllib，无额外依赖）
├── uploads.py         安全上传（魔数校验/Pillow 重编码/随机文件名）
├── markdown_utils.py  Markdown → bleach 消毒 → HTML
├── gameconstants.py   游戏领域常量（与游戏 JSON 字段一一对应）
├── api.py             JSON API（投票/评论/举报/预览）
├── admin.py           管理后台
├── users.py           用户主页/设置
├── forum.py           社区论坛（帖子列表/发帖/详情，回复复用评论系统）
├── misc.py            首页/搜索/sitemap/robots
├── templates/         Jinja2 模板（服务端渲染，SEO 友好）
└── static/            WW1 军事风格 CSS / JS / 字体
data/                  数据库 + 上传文件（gitignore）
seed/cards/            游戏 JSON 内置副本（导入回退源）
tests/test_site.py     87 项自动化测试
manage.py              init-db / import-cards / create-admin / create-labels / stats
wsgi.py / run.py       生产入口(waitress) / 开发入口
scripts/*.ps1          安装 / 初始化 / 启动脚本
```

## 安全要点

- SQL 全部参数化；XSS 经 Jinja 自动转义 + bleach 白名单消毒；CSRF 全站强制（表单 + API）
- 密码 Argon2id；会话令牌存库可吊销（封禁/改密强制下线）；Cookie HttpOnly + SameSite + 生产 Secure
- 登录限速：用户名/IP 双维度，15 分钟窗口 5 次失败锁定；全站每 IP 每秒 30 请求节流
- 上传：仅图片扩展名 + 魔数嗅探 + Pillow 校验重编码 + 大小限制 + 随机文件名 + nosniff 头
- `.env` 不入库；开放重定向防护；上传文件只经 `send_from_directory` 提供（防路径穿越）
