r"""游戏领域常量 — 与 D:\Code\1914 的 data/cards JSON 字段一一对应。
展示文案来自 docs/game-mechanics.md，不发明游戏中不存在的字段。
"""
import re

# 单位类型（卡牌 type 字段）
CARD_TYPES = {
    "unit": "单位",
    "order": "指令",
}

# 兵种（unit_class 字段）
UNIT_CLASSES = {
    "infantry": "步兵",
    "cavalry": "骑兵",
    "tank": "坦克",
    "fighter": "战斗机",
    "bomber": "轰炸机",
    "artillery": "火炮",
    "fortification": "工事",
}
UNIT_CLASS_ICONS = {
    "infantry": "🎖",
    "cavalry": "🐎",
    "tank": "🛡",
    "fighter": "✈",
    "bomber": "💥",
    "artillery": "🎯",
    "fortification": "🏰",
}

# 稀有度（rarity 字段）— 游戏：铜/银/金
RARITIES = {
    "common": "铜",
    "silver": "银",
    "gold": "金",
}

# 词条（abilities 字段）— 说明来自 game-mechanics.md 第 2 节
ABILITIES = {
    "突击": "本单位进入战场后可以直接操作。",
    "坚守": "交战时，实际受到伤害 = 原伤害 − 坚守等级（1–3）。坦克默认坚守 1。",
    "冲锋": "本单位首次攻击时，不受到反击伤害。",
    "收缴": "消灭单位时，获得其 50% 生产经济 G（默认 25%）。",
    "守护": "为相邻友方单位提供“被守护”，代其承受周围八格部队的伤害。",
    "响应": "本单位生产后可以在同一回合部署。",
    "防空": "与空军交战时可反击，且视为与射程范围内步兵交战。",
    "补给": "友方回合开始时，修复一个已受伤的友方单位。",
    "修复": "可恢复 x 点防御力。",
    "潜行": "敌方无法观察本单位，直到“被发现”。",
}

# 阵营（nation 字段）— 游戏目前仅中立；沙俄→苏俄机制已规划，先预留映射
NATIONS = {
    "neutral": "中立",
    "russia_empire": "沙俄",
    "soviet_russia": "苏俄",
    "german_empire": "德意志帝国",
    "french_republic": "法兰西",
    "british_empire": "大英帝国",
    "austria_hungary": "奥匈帝国",
    "ottoman_empire": "奥斯曼帝国",
}

# 视野 / 攻击范围（vision_range / attack_range 字段）
RANGES = {
    "adjacent_4": "周围四格",
    "adjacent_8": "周围八格",
    "adjacent_8_forward": "周围八格及向前一格",
    "front_3x2": "前方横向 3×2 格",
    "front_3x3": "前方横向 3×3 格",
    "column_and_neighbors": "所在列及相邻两列",
    "global": "全图",
    "own_line": "所在阵线",
    "none": "无",
}

# Issue 状态 / 优先级
ISSUE_STATUSES = {
    "open": "Open",
    "in_progress": "In Progress",
    "resolved": "Resolved",
    "closed": "Closed",
    "duplicate": "Duplicate",
}
ISSUE_STATUS_COLORS = {
    "open": "#4a9e4a",
    "in_progress": "#c9a227",
    "resolved": "#3a7bbf",
    "closed": "#777777",
    "duplicate": "#9b59b6",
}
ISSUE_PRIORITIES = {
    "none": "无",
    "low": "低",
    "medium": "中",
    "high": "高",
    "critical": "严重",
}
ISSUE_PRIORITY_COLORS = {
    "none": "#777777",
    "low": "#3a7bbf",
    "medium": "#c9a227",
    "high": "#d35400",
    "critical": "#b03030",
}

# 卡牌性质（tag 字段）——按游戏版本自动判定（见 official_tag_for_version），
# 悬停或点击徽章显示说明
CARD_TAGS = {
    "正式": "正式卡牌：游戏 v1.0 起进入正式卡池的卡牌。",
    "测试": "测试卡牌：游戏 v0.x 阶段的卡牌（含显式测试卡），不代表正式卡牌和样板数据。",
}
DEFAULT_CARD_TAG = "正式"


def official_tag_for_version(version: str) -> str:
    """官方卡性质判定：游戏版本号 v0.* 一律为测试卡，v1.0 起才是正式卡。
    版本号允许 v/V 前缀；无法解析时按未到 v1 处理（测试）。
    ID/名称带显式测试标记的卡在任何版本都保持测试卡（由调用方叠加判断）。
    """
    m = re.match(r"^v?(\d+)", (version or "").strip().lower())
    return "正式" if (m and int(m.group(1)) >= 1) else "测试"

# Issue 所属（决定 GitHub 同步分流到哪个仓库）
ISSUE_COMPONENTS = {
    "game": "游戏本体",
    "web": "网页",
}
DEFAULT_ISSUE_COMPONENT = "game"

# Issue 预设标签（提交页逐个陈列供勾选；写入 labels/content_labels 复用标签系统）
ISSUE_TAGS = {
    "兼容性": "系统、驱动或运行环境兼容问题",
    "平衡性": "卡牌强度与对局平衡问题",
    "崩溃": "游戏崩溃、闪退、无响应",
    "数值错误": "攻击/防御/费用等数值不正确",
    "UI 显示": "界面布局、显示异常",
    "联网对战": "联机、匹配、网络问题",
    "存档": "存档丢失或进度异常",
    "性能": "卡顿、掉帧、加载缓慢",
    "文案": "错别字、翻译、描述文本问题",
    "功能建议": "新功能与改进建议",
}

# 论坛板块 — forum_posts.category 即板块标识（不另建 Board 表）。
# 顺序 = 论坛首页板块卡片的展示顺序；desc 显示在板块卡片内（弱化文字）。
FORUM_BOARDS = (
    {"name": "网站更新日志", "icon": "📢", "group": "官方信息",
     "desc": "网站功能、修复与改版记录", "admin_only": True},
    {"name": "游戏更新日志", "icon": "🎮", "group": "官方信息",
     "desc": "游戏版本更新、平衡调整与 Bug 修复", "admin_only": True},
    {"name": "游戏机制", "icon": "⚙️", "group": "游戏讨论",
     "desc": "游戏玩法、规则、资源与战斗机制讨论"},
    {"name": "游戏攻略", "icon": "📖", "group": "游戏讨论",
     "desc": "玩法教学、卡组思路与战术攻略"},
    {"name": "游戏资料", "icon": "📚", "group": "游戏讨论",
     "desc": "机制资料、单位资料与历史背景"},
    {"name": "卡牌设计", "icon": "🃏", "group": "卡牌社区",
     "desc": "卡牌设计理念、数值与效果讨论"},
    {"name": "卡牌修改提案", "icon": "🛠️", "group": "卡牌社区",
     "desc": "对官方卡牌提交修改提案，社区投票 + 管理员审核",
     "proposal": "modification"},
    {"name": "卡牌转正", "icon": "⭐", "group": "卡牌社区",
     "desc": "玩家自制卡牌申请转为官方正式卡牌", "proposal": "promotion"},
    {"name": "创作分享", "icon": "🎨", "group": "玩家社区",
     "desc": "玩家作品、同人图、视频与 MOD 分享"},
    {"name": "闲聊", "icon": "💬", "group": "玩家社区",
     "desc": "与游戏无关的日常闲谈"},
)
FORUM_BOARD_MAP = {b["name"]: b for b in FORUM_BOARDS}
FORUM_CATEGORIES = tuple(b["name"] for b in FORUM_BOARDS)
PROPOSAL_CATEGORIES = {b["name"]: b["proposal"] for b in FORUM_BOARDS if b.get("proposal")}
ADMIN_ONLY_CATEGORIES = tuple(b["name"] for b in FORUM_BOARDS if b.get("admin_only"))
POSTABLE_CATEGORIES = tuple(n for n in FORUM_CATEGORIES if n not in PROPOSAL_CATEGORIES)
DEFAULT_FORUM_CATEGORY = "游戏机制"
# 历史分类 → 新板块（幂等迁移，仅 UPDATE 不删除；见 db._migrate）
FORUM_CATEGORY_MIGRATION = {
    "讨论": "游戏机制", "攻略": "游戏攻略", "求助": "游戏机制", "建议": "闲聊",
}

# 卡牌提案状态（card_proposals.status）
PROPOSAL_STATUSES = {
    "active": "讨论中",      # 活跃讨论，等待管理员审核
    "returned": "已打回",    # 管理员打回修改，作者可重新编辑提交
    "approved": "已批准",    # 已应用/转正（帖子归档冻结）
    "rejected": "未批准",    # 不批准（帖子归档冻结）
}
PROPOSAL_TYPES = {"modification": "修改提案", "promotion": "转正提案"}

ROLES = ("user", "moderator", "admin")


def ability_name(token: str) -> str:
    """"坚守2" -> "坚守"，“补给” -> "补给"。"""
    for base in ABILITIES:
        if token == base or (token.startswith(base) and token[len(base):].isdigit()):
            return base
    return token


def ability_level(token: str) -> int:
    """"坚守2" -> 2，"坚守" -> 1。与游戏 _parse_ability_level 约定一致。"""
    for base in ABILITIES:
        if token.startswith(base):
            tail = token[len(base):]
            return int(tail) if tail.isdigit() else 1
    return 1


def display_range(key: str) -> str:
    return RANGES.get(key, key or "—")
