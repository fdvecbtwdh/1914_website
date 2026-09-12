r"""游戏领域常量 — 与 D:\Code\1914 的 data/cards JSON 字段一一对应。
展示文案来自 docs/game-mechanics.md，不发明游戏中不存在的字段。
"""

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

# 卡牌性质（tag 字段）——投稿/导入时选择，悬停或点击徽章显示说明
CARD_TAGS = {
    "正式": "正式卡牌：进入游戏正式卡池的卡牌。",
    "测试": "测试卡牌：测试时使用的卡牌，不代表正式卡牌和样板数据。",
}
DEFAULT_CARD_TAG = "正式"

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
