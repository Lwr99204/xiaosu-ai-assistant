-- 小苏 SQLite schema（TDD §4.7）。启动时由 engine.init_db 执行。
-- 全部 IF NOT EXISTS：对已有库幂等，可重复执行。
-- conversations.status 不加 CHECK：代码层允许 ok / degraded / refused 三种取值
-- （refused 为主动拒答，是 TDD 注释 ok/degraded/error 的细化，见 agent_service.py）。

CREATE TABLE IF NOT EXISTS documents (                        -- 文档元数据 + 索引状态机
  doc_id        TEXT PRIMARY KEY,               -- 归一化文件名 = 唯一键（支撑增量更新）
  filename      TEXT NOT NULL,
  ext           TEXT NOT NULL,
  byte_size     INTEGER NOT NULL,
  content_hash  TEXT NOT NULL,                  -- SHA-256 → FR-1-07 去重
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','indexed','failed')),
  chunk_count   INTEGER NOT NULL DEFAULT 0,
  version       INTEGER NOT NULL DEFAULT 1,     -- 每次替换 +1，便于审计
  error_message TEXT,
  storage_path  TEXT,                           -- 原文件落盘路径（供前端预览原文高亮）
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  indexed_at    TEXT
);

CREATE TABLE IF NOT EXISTS conversations (                    -- 一轮问答 = 一行
  id                INTEGER PRIMARY KEY AUTOINCREMENT,
  request_id        TEXT NOT NULL,
  session_key       TEXT NOT NULL,
  platform          TEXT NOT NULL,              -- wecom / feishu / web
  user_id           TEXT NOT NULL,
  user_name         TEXT,
  question          TEXT NOT NULL,
  answer            TEXT,
  citations_json    TEXT,                       -- 引用数组（含 char_start/char_end）
  refused_by        TEXT,                       -- threshold / no_citation / sensitive / NULL
  status            TEXT NOT NULL,              -- ok / degraded / refused
  error_code        TEXT,
  model             TEXT,
  prompt_tokens     INTEGER DEFAULT 0,
  completion_tokens INTEGER DEFAULT 0,
  total_tokens      INTEGER DEFAULT 0,
  cost_cny          REAL    DEFAULT 0,          -- FR-5-10 成本展示
  latency_ms        INTEGER,
  created_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_conv_session ON conversations(session_key, id);  -- 上下文读取
CREATE INDEX IF NOT EXISTS idx_conv_created ON conversations(created_at);       -- 后台时间筛选

CREATE TABLE IF NOT EXISTS tool_calls (                       -- FR-3-09 / FR-5-04
  id              INTEGER PRIMARY KEY AUTOINCREMENT,
  conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
  round           INTEGER NOT NULL,
  tool_name       TEXT NOT NULL,
  arguments_json  TEXT NOT NULL,
  result_summary  TEXT,                         -- 截断至 500 字，防日志爆炸
  ok              INTEGER NOT NULL,
  error           TEXT,
  latency_ms      INTEGER,
  created_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS processed_messages (               -- IM 幂等去重（FR-4-11）
  message_id TEXT PRIMARY KEY,
  platform   TEXT NOT NULL,
  created_at TEXT NOT NULL
);
