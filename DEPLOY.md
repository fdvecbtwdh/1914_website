# 1914.fun Windows Server 2022 完整部署指南

目标：在一台**全新的 Windows Server 2022** 上，把 1914.fun 网站跑起来并通过 Cloudflare Tunnel 对外提供服务。

```
用户 → https://1914.fun → Cloudflare → cloudflared(新服务器) → 127.0.0.1:8000 (waitress) → SQLite
```

整个过程约 30–60 分钟。按顺序做，每步都有验证点。

> ## ⚡ 一键部署（推荐先看这里）
>
> 复制项目文件到服务器（第 3 步）后，**管理员 PowerShell** 进入项目目录执行：
>
> ```powershell
> powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1
> ```
>
> 这一条命令会自动完成本指南的 **第 2、4、5、6、8 步**：检测/安装 Python →
> 装依赖 → 生成 .env（随机密钥、路径自适应部署目录、生产默认值）→ 初始化数据库
> → 导入官方卡牌 → 建管理员（交互输入）→ 下载 NSSM 并注册 `1914site` 系统服务（开机自启）。
>
> 可选参数：
>
> ```powershell
> # 连 cloudflared 隧道一起配（token 来自 Zero Trust 仪表盘创建隧道后的安装命令）
> powershell -ExecutionPolicy Bypass -File scripts\deploy.ps1 -CloudflaredToken "eyJ..."
>
> # 其他开关：-SkipService（不装服务）  -SkipTunnel（不配隧道）
> #           -SkipPythonCheck          -AdminUser x -AdminPassword y（非交互建管理员）
> ```
>
> 脚本幂等，重复运行安全。跑完后只需做 **第 9 步的仪表盘 Public Hostname 配置**（方案 A）
> 和 **第 10 步上线验证**。下面的分步内容适合想理解每一步、或需要手动处理特殊情况的场景。

---

## 目录

1. [准备清单](#1-准备清单)
2. [安装 Python](#2-安装-python)
3. [复制项目文件](#3-复制项目文件)
4. [安装依赖](#4-安装依赖)
5. [配置 .env](#5-配置-env)
6. [初始化数据库与管理员](#6-初始化数据库与管理员)
7. [本机验证](#7-本机验证)
8. [注册网站为系统服务（开机自启）](#8-注册网站为系统服务开机自启)
9. [部署 Cloudflare Tunnel](#9-部署-cloudflare-tunnel)
10. [上线验证](#10-上线验证)
11. [日常维护](#11-日常维护)
12. [故障排查](#12-故障排查)

---

## 1. 准备清单

| 项目 | 说明 |
|------|------|
| 服务器 | Windows Server 2022，具备 Administrator 权限 |
| 项目文件 | 开发机 `D:\Code\1914_website` 整个目录（可不含 `.venv` 和 `data`，见第 3 步） |
| 网络 | 服务器能访问互联网（下载 Python / 依赖 / cloudflared） |
| Cloudflare 账号 | `1914.fun` 域名已托管在 Cloudflare；已有可用 Tunnel 更佳 |
| 管理员初始密码 | 部署时自己设定（第 6 步会提示输入） |

> **注意**：新服务器上**没有**游戏项目 `D:\Code\1914`，这不影响运行——
> 网站会自动使用项目内置的 `seed/cards/` 卡牌副本（16 张官方卡）。
> 以后游戏卡牌改了，把新的卡牌 JSON 复制到服务器的 `seed\cards\` 覆盖，
> 再到后台点「从游戏项目导入官方卡牌」即可（或见第 11 节）。

---

## 2. 安装 Python

Server 2022 默认没有 Python，也不带 winget，用官网安装包最稳。

以**管理员身份**打开 PowerShell：

```powershell
# 下载 Python 3.12（3.11+ 均可）
Invoke-WebRequest "https://www.python.org/ftp/python/3.12.8/python-3.12.8-amd64.exe" -OutFile "$env:TEMP\python-setup.exe"

# 静默安装：所有用户 + 加入 PATH
& "$env:TEMP\python-setup.exe" /quiet InstallAllUsers=1 PrependPath=1 Include_test=0

# 重开一个 PowerShell 窗口后验证（PATH 刷新需要新窗口）
python --version    # 应显示 Python 3.12.8
```

**验证点**：`python --version` 有输出即成功。如果提示找不到 python，重新开一个 PowerShell 窗口再试。

> 不要用 Microsoft Store 版 Python（服务化后会有路径/权限问题）。

---

## 3. 复制项目文件

在**开发机**上打包（排除虚拟环境和运行数据），任意方式传到服务器（RDP 剪贴板 / 网络共享 / U 盘均可）：

```powershell
# 开发机上执行：打一个干净的 zip（排除 .venv / data / 缓存）
$src = "D:\Code\1914_website"
$dst = "$env:TEMP\1914_website.zip"
Compress-Archive -Path $src -Destination $dst -Force
# 然后 RDP 拖拽或复制到服务器，解压到例如 C:\1914_website
```

也可以用 robocopy 直拷（在两台机器有网络共享时）：

```powershell
robocopy D:\Code\1914_website \\新服务器\c$\1914_website /E /XD .venv data __pycache__ .pytest_cache
```

> **迁移已有数据（可选）**：如果不想从零开始，而是把现在开发机上已有的
> 用户/卡牌/Issue 一起搬过去，把开发机 `data\` 目录（含 `1914.db` 和 `uploads\`）
> 一并复制到服务器 `C:\1914_website\data\`，然后**跳过第 6 步**。
> 注意：搬了旧数据，旧的管理员密码照旧；没搬则按第 6 步新建。

以下假设项目位于 **`C:\1914_website`**（放别的盘同理替换路径）。

---

## 4. 安装依赖

服务器上以管理员打开 PowerShell：

```powershell
cd C:\1914_website

# 允许运行项目自带脚本
Set-ExecutionPolicy -Scope Process Bypass

# 一键创建虚拟环境 + 安装依赖（官方源失败会自动换清华镜像）
powershell -ExecutionPolicy Bypass -File scripts\install.ps1

# 验证
.\.venv\Scripts\python.exe -c "import flask, waitress, argon2, markdown, bleach, PIL; print('deps OK')"
```

**验证点**：输出 `deps OK`。

> 手动等价命令（如需自己敲）：
> ```powershell
> python -m venv .venv
> .\.venv\Scripts\python.exe -m pip install --upgrade pip
> .\.venv\Scripts\python.exe -m pip install -r requirements.txt --index-url https://pypi.org/simple
> # 若官方源超时/SSL 报错，改用：
> .\.venv\Scripts\python.exe -m pip install -r requirements.txt --index-url https://pypi.tuna.tsinghua.edu.cn/simple
> ```

---

## 5. 配置 .env

```powershell
# 从模板生成（若第 3 步已带过来 .env 则跳过）
Copy-Item .env.example .env
notepad C:\1914_website\.env
```

在记事本中**必须修改**的项：

```ini
# 会话密钥：改成随机值（下面第 5.1 步有生成命令）
SESSION_SECRET=<粘贴一长串随机字符>

# 对外地址（保持不变即可）
SITE_URL=https://1914.fun
SITE_DOMAIN=1914.fun
# ↑ 站点域名集中配置：SITE_URL 驱动 canonical/OG/sitemap/robots/邮件链接/GitHub 同步来源；
#   SITE_DOMAIN 驱动页头 logo/页脚/og:site_name/恢复邮件主题。
#   更换域名 = 改这两行 + 重启服务 + Cloudflare Tunnel 指向新域名（业务代码无需修改）。

# 路径改成服务器上的实际位置（注意用正斜杠）
DATABASE_URL=sqlite:///C:/1914_website/data/1914.db
UPLOAD_DIR=C:/1914_website/data/uploads

# 游戏 JSON 来源：服务器上没有游戏项目，清空让它用内置 seed/
GAME_PROJECT_PATH=

# 生产 HTTPS，必须为 1（登录 cookie 只经 HTTPS 传输）
COOKIE_SECURE=1

# 只监听本机：站点仅通过 Tunnel 对外，不暴露局域网端口
HOST=127.0.0.1
PORT=8000
```

**5.1 生成随机密钥**（在 PowerShell 里执行，把输出复制进 .env）：

```powershell
-join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })
```

**5.2 数据库自动恢复与默认模板**：

```powershell
# 把当前数据库保存为"重置默认"（之后删库重启即恢复为此状态；网站运行中也可执行）
.venv\Scripts\python.exe manage.py save-default
```

保存后模板位于 `data\default.db`。网站启动时若检测到数据库文件不存在：

- 有 `data\default.db` 模板 → **整库恢复**为保存时的完整状态（含账号密码、卡牌性质、全部数据）
- 无模板 → 自动播种：16 张官方卡牌 + 默认标签 + 初始管理员 `admin`（随机密码打印到启动日志，搜 `[1914]`）

已有数据的数据库不会被改动。此行为可用 `.env` 中 `AUTO_SEED=0` 关闭（模板恢复不受此开关影响）。

**5.3 游戏服务器状态页（可选）**：状态页 `/status` 的"游戏服务器"面板需要游戏服务端
按协议上报数据。在 `.env` 中设置 `GAME_SERVER_TOKEN=<随机字符串>` 启用接收接口，
协议见游戏项目 `docs/web-integration.md`；不设置则该面板显示"不可用"，网站其余功能不受影响。

**5.4 GitHub 同步（可选）**：想要网站 Issue 推送到 GitHub，在 `.env` 里填
`GITHUB_TOKEN=ghp_xxx`（GitHub → Settings → Developer settings → PAT classic，勾 `repo` 权限）。
不填则该功能静默关闭，站点一切正常。
Issue 按所属分流：**游戏本体 Issue → `GITHUB_REPO`（默认 1914）**，
**网页 Issue → `GITHUB_WEB_REPO`（默认 1914_website）**，两个仓库共用同一 Token。
服务器无法直连 GitHub 时，在 `.env` 设
`GITHUB_PROXY=http://127.0.0.1:7890`（Clash 的混合端口），同步流量自动走代理。

**5.5 邮件（可选；账户恢复用）**：启用玩家"忘记密码 → 邮箱找回"，在 `.env` 填：

```ini
MAIL_HOST=smtp-relay.brevo.com     # 或 smtp.qq.com / smtp.163.com
MAIL_PORT=587                      # 465 端口改用 MAIL_USE_SSL=1
MAIL_USER=你的登录名
MAIL_PASSWORD=授权码或应用密码       # QQ/163 是授权码，不是邮箱登录密码
MAIL_FROM=已验证的发件邮箱
MAIL_USE_TLS=1
```

- `MAIL_FROM` 必须是邮件服务商验证过的发件地址
- 不配置 `MAIL_HOST` 则邮箱恢复自动停用，玩家仍可用安全问题找回

---

## 6. 初始化数据库与管理员

```powershell
cd C:\1914_website

# 交互式输入管理员用户名和密码（密码至少 8 位，含字母和数字）
.\.venv\Scripts\python.exe manage.py init-db
.\.venv\Scripts\python.exe manage.py import-cards     # 导入 16 张官方卡
.\.venv\Scripts\python.exe manage.py create-labels
.\.venv\Scripts\python.exe manage.py create-admin     # ← 记住这个密码

# 或一条龙（init.ps1 会在 .env 缺失时自动生成并填随机密钥）：
powershell -ExecutionPolicy Bypass -File scripts\init.ps1
```

**验证点**：`manage.py stats` 显示 `cards: 16`。

---

## 7. 本机验证

```powershell
# 前台启动（临时）
powershell -ExecutionPolicy Bypass -File scripts\start-prod.ps1
```

另开一个 PowerShell 窗口验证：

```powershell
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/       # 应输出 200
curl.exe -s http://127.0.0.1:8000/cards | findstr "步兵"          # 应能看到卡牌名
```

再用服务器上的浏览器打开 `http://127.0.0.1:8000`，用管理员登录一次，
**立即到 `/settings` 改掉初始密码**。确认没问题后关掉临时窗口（Ctrl+C 或 `taskkill /F /IM python.exe`）。

---

## 8. 注册网站为系统服务（开机自启）

用 NSSM 把 waitress 挂成 Windows 服务，崩溃自动重启、开机自启。

```powershell
# 8.1 下载 NSSM（官网 https://nssm.cc/release/nssm-2.24.zip）
Invoke-WebRequest "https://nssm.cc/release/nssm-2.24.zip" -OutFile "$env:TEMP\nssm.zip"
Expand-Archive "$env:TEMP\nssm.zip" "$env:TEMP\nssm" -Force
Copy-Item "$env:TEMP\nssm\nssm-2.24\win64\nssm.exe" "C:\Windows\System32\nssm.exe"

# 8.2 注册服务
nssm install 1914site "C:\1914_website\.venv\Scripts\python.exe" "C:\1914_website\wsgi.py"
nssm set 1914site AppDirectory C:\1914_website
nssm set 1914site AppEnvironmentExtra HOST=127.0.0.1 PORT=8000 PYTHONOPTIMIZE=1
nssm set 1914site AppStdout C:\1914_website\data\service.log
nssm set 1914site AppStderr C:\1914_website\data\service.log
nssm set 1914site AppRotateFiles 1
nssm set 1914site Start SERVICE_AUTO_START

# 8.3 启动并验证
nssm start 1914site
curl.exe -s -o NUL -w "%{http_code}" http://127.0.0.1:8000/      # 应输出 200
```

服务管理常用命令：

```powershell
nssm restart 1914site     # 重启（更新代码后用）
nssm stop 1914site        # 停止
nssm status 1914site      # 查看状态
Get-Content C:\1914_website\data\service.log -Tail 30   # 看日志
```

> 防火墙说明：站点只监听 127.0.0.1，**无需**开放任何入站端口——
> 流量全部走 cloudflared 本机回环，这是最安全的姿态。
> 如果确实需要局域网直接访问（不推荐），把 HOST 改为 0.0.0.0 并放行 8000 端口。

---

## 9. 部署 Cloudflare Tunnel

按你的情况二选一。

### 方案 A：仪表盘托管隧道（推荐，最简单）

适合：想在 Cloudflare 后台集中管理，或第一次给这台服务器配隧道。

1. 浏览器打开 Cloudflare Zero Trust 控制台 → **Networks → Tunnels → Create a tunnel** → 选 **Cloudflared**，起名如 `win2022`
2. 创建后页面给出安装命令，复制其中的 **token**（很长的 `eyJ...`）
3. 服务器上（管理员 PowerShell）：
   ```powershell
   Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile "C:\Windows\System32\cloudflared.exe"
   cloudflared service install <把token粘贴在这里>
   ```
4. 回到仪表盘该隧道的 **Public Hostname** 标签 → Add a public hostname：
   - Subdomain: `（留空，用根域）`，Domain: `1914.fun`
   - Service: `HTTP` → `localhost:8000`
   - 再加一条 `www.1914.fun` → 同样指向 `localhost:8000`（如果 DNS 有 www 记录）
5. 仪表盘隧道状态显示 **HEALTHY** 即成功

> 若 `1914.fun` 的 DNS 之前已指向旧隧道的 CNAME，在 Cloudflare DNS 里把
> `1914.fun` 的 CNAME 改为指向新隧道（`<新隧道ID>.cfargotunnel.com`），
> 或直接删掉旧记录由 Public Hostname 自动创建。

### 方案 B：迁移现有本地托管隧道

适合：你现在这台机器上已经用 `config.yml + 凭据 JSON` 方式跑着隧道，想原样搬过去。

1. 在**旧机器**找到 `~/.cloudflared` 目录（通常 `C:\Users\<你>\.cloudflared\`），里面有：
   - `cert.pem`（账号证书）
   - `<隧道UUID>.json`（隧道凭据）
   - `config.yml`
2. 复制到新服务器，同时放到两个位置（服务以 LocalSystem 运行，读取系统配置目录）：
   ```powershell
   # 服务器上执行（替换 <你> 与隧道UUID）
   mkdir C:\Users\<你>\.cloudflared -Force
   copy "<来源>\cert.pem"          C:\Users\<你>\.cloudflared\
   copy "<来源>\<UUID>.json"       C:\Users\<你>\.cloudflared\
   # 服务运行时读取的位置：
   mkdir C:\Windows\System32\config\systemprofile\.cloudflared -Force
   copy C:\Users\<你>\.cloudflared\* C:\Windows\System32\config\systemprofile\.cloudflared\
   ```
3. 新服务器的 `config.yml`（两处目录各放一份）：
   ```yaml
   tunnel: <隧道UUID>
   credentials-file: C:\Windows\System32\config\systemprofile\.cloudflared\<隧道UUID>.json
   ingress:
     - hostname: 1914.fun
       service: http://localhost:8000
     - hostname: www.1914.fun
       service: http://localhost:8000
     - service: http_status:404
   ```
4. 安装 cloudflared 并注册服务：
   ```powershell
   Invoke-WebRequest "https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe" -OutFile "C:\Windows\System32\cloudflared.exe"
   cloudflared service install
   nssm 不需要——cloudflared 自带服务；确认状态：
   sc.exe query cloudflared
   ```
5. **注意**：同一条隧道不要新旧两台机器同时跑（会抢连接）。旧机器上先停掉：
   `cloudflared service stop` 或卸载旧服务。

> GitHub 下载 cloudflared 慢的话，可用镜像：
> `https://ghproxy.com/https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe`

---

## 10. 上线验证

在一台**外网**设备（手机流量即可）上：

```powershell
# 1. 站点可达
curl.exe -s -o NUL -w "%{http_code}" https://1914.fun            # 200
# 2. 卡牌页有数据
curl.exe -s https://1914.fun/cards | findstr "步兵"
# 3. SEO 文件
curl.exe -s -o NUL -w "%{http_code}" https://1914.fun/sitemap.xml   # 200
curl.exe -s -o NUL -w "%{http_code}" https://1914.fun/robots.txt    # 200
# 4. 状态页（网站面板应显示"在线"与真实 CPU/内存/GPU 数据）
curl.exe -s -o NUL -w "%{http_code}" https://1914.fun/status        # 200
```

浏览器走查一遍：

- [ ] 首页正常、版本号正确
- [ ] 卡牌列表三种视图可切换，官方/自制分区正常
- [ ] 注册一个测试账号 → 投稿卡牌 → 提交 Issue → 评论 → 投票
- [ ] 管理员登录 → 后台筛选/批量操作正常
- [ ] 状态页 /status 数据正常（CPU/内存/GPU/系统运行时间）
- [ ] 手机访问排版正常

**最后确认 `.env` 里 `COOKIE_SECURE=1`**（登录 cookie 仅走 HTTPS）。

---

## 11. 日常维护

### 更新网站代码

```powershell
nssm stop 1914site
# 覆盖 app\ 目录与 wsgi.py / requirements.txt 等代码文件（不要覆盖 .env 和 data\）
.venv\Scripts\python.exe -m pip install -r requirements.txt    # 依赖有变时
nssm start 1914site
```

### 同步游戏卡牌数值

游戏里的卡牌 JSON 改动后，任选其一：

1. 把新的 JSON 覆盖到服务器 `C:\1914_website\seed\cards\`，后台点「从游戏项目导入官方卡牌」；
2. 或把整个游戏项目拷到服务器（如 `C:\game1914`），`.env` 里设 `GAME_PROJECT_PATH=C:\game1914`，再点导入。

### 数据备份（建议做计划任务）

需要备份的只有 `C:\1914_website\data\`（SQLite 库 + 上传图片 + 日志）：

```powershell
# 示例：每天 03:00 备份到 D:\backup（schtasks 一行搞定）
schtasks /Create /TN "Backup1914" /SC DAILY /ST 03:00 /RU SYSTEM ^
  /TR "robocopy C:\1914_website\data D:\backup\1914\data /MIR /R:2 /W:5"
```

恢复 = 停服务 → 还原 `data\` → 起服务。

### 看运行状态

```powershell
nssm status 1914site
Get-Content C:\1914_website\data\service.log -Tail 50
sc.exe query cloudflared
.venv\Scripts\python.exe manage.py stats     # 数据统计
```

---

## 12. 故障排查

| 现象 | 原因 / 处理 |
|------|-------------|
| `python` 不是内部命令 | 装完 Python 没开新窗口；或安装时没勾 PrependPath，重装勾上 |
| pip 安装 SSL/超时报错 | 用清华镜像：`pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple` |
| 无法加载脚本（ExecutionPolicy） | `Set-ExecutionPolicy -Scope Process Bypass` 或命令前加 `powershell -ExecutionPolicy Bypass` |
| 服务起不来 | 看 `data\service.log`；常见是 `.env` 路径错或 8000 被占：`netstat -ano \| findstr :8000` |
| 本机 200 但 1914.fun 打不开 | cloudflared 未跑通：`sc.exe query cloudflared`；隧道状态去 Zero Trust 仪表盘看；Public Hostname 的 Service 是否 `localhost:8000` |
| 能打开但无法登录 | `.env` 的 `COOKIE_SECURE` 必须为 `1` 且通过 https 访问；换过 `SESSION_SECRET` 会使所有旧会话失效（重新登录即可） |
| 图片上传后 404 | `UPLOAD_DIR` 路径与实际不符（注意用正斜杠 `C:/...`） |
| 官方卡导入为 0 | 服务器没有游戏项目属正常（用内置 seed/）；确需从游戏项目导入则设置 `GAME_PROJECT_PATH` |
| 忘记管理员密码 | 删除 `data\1914.db` 后重启可整体重置（数据清空）；或用其他管理员账号在后台改密 |
| 502 Bad Gateway | 网站服务没起来：`nssm status 1914site`，查 service.log |

---

## 附：一次性部署脚本（可选合并执行）

把第 4–6 步串起来（已安装 Python 的前提下，管理员 PowerShell、位于项目目录执行）：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
powershell -ExecutionPolicy Bypass -File scripts\init.ps1
```

之后按第 8 节注册服务、第 9 节配隧道即可。
