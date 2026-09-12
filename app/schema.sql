-- 1914.fun 数据库结构 (SQLite)
-- 时间戳统一使用 UTC ISO-8601 字符串，便于迁移 PostgreSQL。

PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT    NOT NULL UNIQUE COLLATE NOCASE,
    email         TEXT    UNIQUE COLLATE NOCASE,
    password_hash TEXT    NOT NULL,
    role          TEXT    NOT NULL DEFAULT 'user',   -- user / moderator / admin
    bio           TEXT    NOT NULL DEFAULT '',
    is_banned     INTEGER NOT NULL DEFAULT 0,
    security_question      TEXT,
    security_answer_hash   TEXT,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now')),
    last_login_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    id         TEXT PRIMARY KEY,                 -- 随机 token
    user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    created_at TEXT    NOT NULL DEFAULT (datetime('now')),
    expires_at TEXT    NOT NULL,
    ip         TEXT    NOT NULL DEFAULT '',
    user_agent TEXT    NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expiry ON sessions(expires_at);

-- 卡牌：官方导入 (source='official', game_id 非空) 与玩家投稿 (source='community')
CREATE TABLE IF NOT EXISTS cards (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id      TEXT UNIQUE,                      -- 游戏内 id，如 infantry_01
    slug         TEXT    NOT NULL UNIQUE,          -- URL 友好名
    name         TEXT    NOT NULL,
    nation       TEXT    NOT NULL DEFAULT 'neutral',
    type         TEXT    NOT NULL DEFAULT 'unit',  -- unit / order
    unit_class   TEXT    NOT NULL DEFAULT '',      -- infantry/cavalry/tank/fighter/bomber/artillery/fortification
    cost_g       INTEGER NOT NULL DEFAULT 0,
    cost_z       INTEGER NOT NULL DEFAULT 0,
    attack       INTEGER NOT NULL DEFAULT 0,
    defense      INTEGER NOT NULL DEFAULT 0,
    vision_range TEXT    NOT NULL DEFAULT '',
    attack_range TEXT    NOT NULL DEFAULT '',
    abilities    TEXT    NOT NULL DEFAULT '[]',    -- JSON 数组，可含等级如 "坚守2"
    rarity       TEXT    NOT NULL DEFAULT 'common',-- common/silver/gold
    tag         TEXT    NOT NULL DEFAULT '正式',  -- 卡牌性质：正式/测试（见 gameconstants.CARD_TAGS）
    art_path     TEXT    NOT NULL DEFAULT '',      -- /uploads/cards/xxx.png
    flavor_text  TEXT    NOT NULL DEFAULT '',
    description  TEXT    NOT NULL DEFAULT '',      -- Markdown
    author_id    INTEGER REFERENCES users(id) ON DELETE SET NULL,
    source       TEXT    NOT NULL DEFAULT 'community', -- official / community
    status       TEXT    NOT NULL DEFAULT 'visible',   -- visible / hidden
    view_count   INTEGER NOT NULL DEFAULT 0,
    created_at   TEXT    NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_cards_author ON cards(author_id);
CREATE INDEX IF NOT EXISTS idx_cards_status ON cards(status);

CREATE TABLE IF NOT EXISTS issues (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    title          TEXT NOT NULL,
    body           TEXT NOT NULL DEFAULT '',       -- Markdown
    status         TEXT NOT NULL DEFAULT 'open',   -- open/in_progress/resolved/closed/duplicate
    priority       TEXT NOT NULL DEFAULT 'none',   -- none/low/medium/high/critical
    duplicate_of   INTEGER REFERENCES issues(id) ON DELETE SET NULL,
    game_version   TEXT NOT NULL DEFAULT '',
    sys_info       TEXT NOT NULL DEFAULT '',
    author_id      INTEGER REFERENCES users(id) ON DELETE SET NULL,
    github_number  INTEGER,
    github_url     TEXT,
    github_synced_at TEXT,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_issues_status ON issues(status);
CREATE INDEX IF NOT EXISTS idx_issues_author ON issues(author_id);

CREATE TABLE IF NOT EXISTS labels (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE,
    color TEXT NOT NULL DEFAULT '#6b7a4f'
);

CREATE TABLE IF NOT EXISTS content_labels (
    content_type TEXT NOT NULL,      -- card / issue
    content_id   INTEGER NOT NULL,
    label_id     INTEGER NOT NULL REFERENCES labels(id) ON DELETE CASCADE,
    PRIMARY KEY (content_type, content_id, label_id)
);

CREATE TABLE IF NOT EXISTS comments (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_type TEXT NOT NULL,       -- card / issue
    target_id   INTEGER NOT NULL,
    author_id   INTEGER REFERENCES users(id) ON DELETE SET NULL,
    parent_id   INTEGER REFERENCES comments(id) ON DELETE CASCADE,
    body        TEXT NOT NULL,       -- Markdown（渲染前消毒）
    body_html   TEXT NOT NULL DEFAULT '',
    is_deleted  INTEGER NOT NULL DEFAULT 0,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    edited_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_comments_target ON comments(target_type, target_id);

CREATE TABLE IF NOT EXISTS votes (
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    target_type TEXT NOT NULL,       -- card / issue / comment
    target_id   INTEGER NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (user_id, target_type, target_id)
);
CREATE INDEX IF NOT EXISTS idx_votes_target ON votes(target_type, target_id);

CREATE TABLE IF NOT EXISTS reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    reporter_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
    target_type TEXT NOT NULL,       -- card / issue / comment / user
    target_id   INTEGER NOT NULL,
    reason      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'open',  -- open / resolved / dismissed
    handled_by  INTEGER REFERENCES users(id) ON DELETE SET NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_reports_status ON reports(status);

-- GitHub 同步队列：站点数据库始终是主数据源，同步失败不影响站点
CREATE TABLE IF NOT EXISTS sync_queue (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    issue_id    INTEGER NOT NULL REFERENCES issues(id) ON DELETE CASCADE,
    action      TEXT NOT NULL,     -- create / update / close / reopen
    repo        TEXT,              -- 目标仓库 full-name（如 fdvecbtwdh/1914）；NULL=默认游戏仓库
    payload     TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'pending',  -- pending / done / failed / skipped
    attempts    INTEGER NOT NULL DEFAULT 0,
    last_error  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_sync_queue_status ON sync_queue(status);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    actor_id    INTEGER,
    action      TEXT NOT NULL,
    target_type TEXT NOT NULL DEFAULT '',
    target_id   INTEGER,
    detail      TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- 登录限速 / 防爆破：按 key（如 ip 或用户名）记录失败次数
CREATE TABLE IF NOT EXISTS rate_limits (
    key           TEXT NOT NULL,
    window_start  TEXT NOT NULL,
    fail_count    INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key)
);

-- 账户恢复令牌（邮箱找回密码）：只存令牌哈希，不存原文
CREATE TABLE IF NOT EXISTS recovery_tokens (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    token_hash  TEXT    NOT NULL UNIQUE,
    expires_at  TEXT    NOT NULL,
    used_at     TEXT,
    created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_recovery_tokens_user ON recovery_tokens(user_id);

-- 站内消息（不做逐条已读；用户级 last_viewed_messages_at 记录查看位置）
CREATE TABLE IF NOT EXISTS notifications (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    actor_id     INTEGER REFERENCES users(id) ON DELETE SET NULL,
    type         TEXT NOT NULL,   -- card_comment / comment_reply / card_vote / issue_comment / issue_vote
    card_id      INTEGER,
    issue_id     INTEGER,
    comment_id   INTEGER,
    dedup_key    TEXT UNIQUE,     -- 防重复（同一操作重试只产生一条）
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notifications_rcpt ON notifications(recipient_id, created_at);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL DEFAULT ''
);
