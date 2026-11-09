-- Novabot 数据库 schema v1
-- 建表顺序按外键依赖排，不能乱
-- 所有时间统一 REAL（Unix 秒）
-- 不物理删，用 gone 标记

PRAGMA foreign_keys = ON;

-- ===========================================================================
-- 一、账号与会话
-- ===========================================================================

CREATE TABLE IF NOT EXISTS accounts (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  platform    TEXT NOT NULL DEFAULT 'qq',
  bot_qq      TEXT,
  bot_uin     TEXT,
  nickname    TEXT,
  enabled     INTEGER NOT NULL DEFAULT 0,
  dry_run     INTEGER NOT NULL DEFAULT 0,
  -- OneBot 连接配置（每个账号一套，界面上可改）
  impl        TEXT DEFAULT 'snowluma',
  reverse_mode INTEGER NOT NULL DEFAULT 0,   -- 1 = 我们开服务，等它连过来
  url         TEXT,
  listen      TEXT,
  token       TEXT,
  auto_start  INTEGER NOT NULL DEFAULT 1,
  -- 多开时的端口分配（避免几个号撞车）
  onebot_port INTEGER NOT NULL DEFAULT 0,
  snowluma_webui_port INTEGER NOT NULL DEFAULT 0,
  snowluma_novnc_port INTEGER NOT NULL DEFAULT 0,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  locked      INTEGER NOT NULL DEFAULT 0,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS chats (
  chat_id        TEXT NOT NULL,
  account_id     TEXT NOT NULL,
  platform       TEXT NOT NULL DEFAULT 'qq',
  chat_type      TEXT NOT NULL DEFAULT 'private',
  group_id       TEXT,
  title          TEXT,
  title_history  TEXT,
  owner_qq       TEXT,
  admin_list     TEXT,
  member_count   INTEGER,
  vibe           TEXT,
  topics         TEXT,
  impression     TEXT,
  msg_count      INTEGER NOT NULL DEFAULT 0,
  first_seen     REAL NOT NULL,
  last_seen      REAL NOT NULL,
  -- 多开：同一个群号在不同账号下是两行
  PRIMARY KEY (account_id, chat_id),
  FOREIGN KEY (account_id) REFERENCES accounts(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chats_account ON chats(account_id, last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_chats_group   ON chats(group_id);

-- ===========================================================================
-- 二、海马体（情景记忆）
-- ===========================================================================

CREATE TABLE IF NOT EXISTS episodes (
  id            INTEGER PRIMARY KEY,
  account_id    TEXT NOT NULL,
  platform      TEXT NOT NULL DEFAULT 'qq',
  chat_id       TEXT NOT NULL,
  chat_type     TEXT NOT NULL DEFAULT 'private',
  group_id      TEXT,
  group_name    TEXT,
  sender_qq     TEXT NOT NULL,
  sender_uin    TEXT,
  sender_nick   TEXT,
  sender_card   TEXT,
  sender_remark TEXT,
  sender_level  INTEGER DEFAULT 0,
  text          TEXT,
  raw           TEXT,
  msg_type      TEXT DEFAULT 'text',
  reply_to      INTEGER,
  at_list       TEXT,
  event_at      REAL NOT NULL,
  recv_at       REAL NOT NULL,
  distilled     INTEGER NOT NULL DEFAULT 0,
  distill_attempts INTEGER NOT NULL DEFAULT 0,   -- 蒸馏失败次数，超过 3 次不再重试
  gone          INTEGER NOT NULL DEFAULT 0,
  gone_reason   TEXT,
  gone_at       REAL,          -- 标记过期的时间，真删的宽限期从这里算
  meta          TEXT
);
CREATE INDEX IF NOT EXISTS idx_ep_chat_time   ON episodes(chat_id, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_ep_sender_time ON episodes(sender_qq, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_ep_group       ON episodes(group_id, event_at DESC);
CREATE INDEX IF NOT EXISTS idx_ep_distill     ON episodes(distilled, event_at) WHERE distilled = 0;
CREATE INDEX IF NOT EXISTS idx_ep_alive       ON episodes(gone, event_at DESC) WHERE gone = 0;

-- 全文索引（中文用 trigram）
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_fts USING fts5(
  text,
  content='episodes',
  content_rowid='id',
  tokenize='trigram'
);

CREATE TRIGGER IF NOT EXISTS episodes_ai AFTER INSERT ON episodes BEGIN
  INSERT INTO episodes_fts(rowid, text) VALUES (new.id, new.text);
END;

CREATE TRIGGER IF NOT EXISTS episodes_ad AFTER DELETE ON episodes BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, text) VALUES ('delete', old.id, old.text);
END;

-- 只有正文变了才动全文索引。
-- 没有这个 WHEN，标过期、标已蒸馏这类更新都会整条重写 FTS，批量操作会慢一个量级。
CREATE TRIGGER IF NOT EXISTS episodes_au AFTER UPDATE ON episodes
WHEN old.text IS NOT new.text
BEGIN
  INSERT INTO episodes_fts(episodes_fts, rowid, text) VALUES ('delete', old.id, old.text);
  INSERT INTO episodes_fts(rowid, text) VALUES (new.id, new.text);
END;

-- ===========================================================================
-- 三、杏仁核（情绪）
-- ===========================================================================

CREATE TABLE IF NOT EXISTS emotions (
  id          INTEGER PRIMARY KEY,
  episode_id  INTEGER NOT NULL,
  sender_qq   TEXT NOT NULL,
  chat_id     TEXT NOT NULL,
  emotion     TEXT DEFAULT 'neutral',
  valence     REAL DEFAULT 0,
  arousal     REAL DEFAULT 0.3,
  confidence  REAL DEFAULT 0.3,
  source      TEXT DEFAULT 'rule',
  created_at  REAL NOT NULL,
  FOREIGN KEY (episode_id) REFERENCES episodes(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_em_episode ON emotions(episode_id);
CREATE INDEX IF NOT EXISTS idx_em_sender  ON emotions(sender_qq, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_em_type    ON emotions(emotion, created_at DESC);

CREATE TABLE IF NOT EXISTS emotion_baseline (
  sender_qq    TEXT PRIMARY KEY,
  avg_valence  REAL NOT NULL DEFAULT 0,
  avg_arousal  REAL NOT NULL DEFAULT 0.3,
  sample_count INTEGER NOT NULL DEFAULT 0,
  updated_at   REAL NOT NULL
);

-- ===========================================================================
-- 四、新皮层（语义记忆）
-- ===========================================================================

CREATE TABLE IF NOT EXISTS facts (
  id            INTEGER PRIMARY KEY,
  account_id    TEXT NOT NULL,
  subject_type  TEXT NOT NULL DEFAULT 'person',
  subject_id    TEXT NOT NULL,
  predicate     TEXT,
  object        TEXT,
  text          TEXT NOT NULL,
  tier          INTEGER NOT NULL DEFAULT 2,
  valid_from    REAL,
  valid_until   REAL,
  recorded_at   REAL NOT NULL,
  superseded_by INTEGER,
  supersedes    INTEGER,
  hits          INTEGER NOT NULL DEFAULT 0,
  last_hit      REAL,
  source_chat   TEXT,
  source_ep     INTEGER,
  confidence    REAL NOT NULL DEFAULT 0.8,
  gone          INTEGER NOT NULL DEFAULT 0,
  FOREIGN KEY (superseded_by) REFERENCES facts(id),
  FOREIGN KEY (source_ep) REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS idx_fact_subject ON facts(account_id, subject_type, subject_id, tier DESC);
CREATE INDEX IF NOT EXISTS idx_fact_valid   ON facts(account_id, subject_id, valid_until);
CREATE INDEX IF NOT EXISTS idx_fact_live    ON facts(account_id, gone, valid_until) WHERE gone = 0;
CREATE INDEX IF NOT EXISTS idx_fact_pred    ON facts(account_id, predicate);
-- 三元组查重（冲突判定第一关：先看是不是同一条）
CREATE INDEX IF NOT EXISTS idx_fact_triple  ON facts(account_id, subject_id, predicate, object);

CREATE TABLE IF NOT EXISTS gists (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  level       INTEGER NOT NULL DEFAULT 1,
  chat_id     TEXT,
  sender_qq   TEXT,
  period_from REAL NOT NULL,
  period_to   REAL NOT NULL,
  text        TEXT NOT NULL,
  source_eps  TEXT,
  created_at  REAL NOT NULL,
  gone        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_gist_chat   ON gists(chat_id, level, period_to DESC);
CREATE INDEX IF NOT EXISTS idx_gist_sender ON gists(sender_qq, level, period_to DESC);

CREATE TABLE IF NOT EXISTS people (
  qq            TEXT NOT NULL,
  account_id    TEXT NOT NULL,
  platform      TEXT NOT NULL DEFAULT 'qq',
  name          TEXT,
  alias         TEXT,
  nickname_hist TEXT,
  card_hist     TEXT,
  style         TEXT,
  persona       TEXT,
  handle        TEXT,
  tags          TEXT,
  msg_count     INTEGER NOT NULL DEFAULT 0,
  first_seen    REAL NOT NULL,
  last_seen     REAL NOT NULL,
  is_owner      INTEGER NOT NULL DEFAULT 0,
  relation_tag  TEXT,
  gone          INTEGER NOT NULL DEFAULT 0,
  -- 人物画像（profile 模块）
  age_group      TEXT,
  occupation     TEXT,
  location       TEXT,
  sleep_pattern  TEXT,
  family         TEXT,
  speech_pattern TEXT,
  mood_baseline  REAL,
  sensitive_topics TEXT,
  trust_level    INTEGER DEFAULT 0,
  last_summary   TEXT,
  updated_at     REAL,
  PRIMARY KEY (qq, account_id)
);
CREATE INDEX IF NOT EXISTS idx_people_alias ON people(alias);
CREATE INDEX IF NOT EXISTS idx_people_seen  ON people(last_seen DESC);

-- 群名片单独存：people.name 永远是 QQ 昵称/本名，群名片不覆盖它
CREATE TABLE IF NOT EXISTS person_group_names (
  qq          TEXT NOT NULL,
  account_id  TEXT NOT NULL,
  group_id    TEXT NOT NULL,
  card        TEXT NOT NULL,
  first_seen  REAL NOT NULL,
  last_seen   REAL NOT NULL,
  PRIMARY KEY (qq, account_id, group_id)
);
CREATE INDEX IF NOT EXISTS idx_pgn_qq ON person_group_names(qq, account_id, last_seen DESC);

-- 人物画像：兴趣与变更历史
CREATE TABLE IF NOT EXISTS person_interests (
  qq          TEXT NOT NULL,
  account_id  TEXT NOT NULL,
  topic       TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'like',
  weight      REAL NOT NULL DEFAULT 0.5,
  last_mention REAL,
  evidence    TEXT,
  created_at  REAL NOT NULL,
  PRIMARY KEY (qq, account_id, topic, kind)
);
CREATE INDEX IF NOT EXISTS idx_pi_qq ON person_interests(qq, account_id, kind, weight DESC);

CREATE TABLE IF NOT EXISTS profile_history (
  id         INTEGER PRIMARY KEY,
  qq         TEXT NOT NULL,
  account_id TEXT NOT NULL,
  field      TEXT NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  changed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ph_qq ON profile_history(qq, changed_at DESC);

-- 指代消解：别名与解析日志
CREATE TABLE IF NOT EXISTS person_aliases (
  alias      TEXT NOT NULL,
  qq         TEXT NOT NULL,
  account_id TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'nickname',
  chat_id    TEXT,
  weight     REAL NOT NULL DEFAULT 1.0,
  first_seen REAL NOT NULL,
  last_seen  REAL NOT NULL,
  PRIMARY KEY (alias, qq, account_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_alias_lookup ON person_aliases(alias, account_id);

CREATE TABLE IF NOT EXISTS pronoun_resolutions (
  id          INTEGER PRIMARY KEY,
  chat_id     TEXT NOT NULL,
  episode_id  INTEGER,
  pronoun     TEXT,
  resolved_to TEXT,
  method      TEXT,
  confidence  REAL,
  created_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS name_ambiguities (
  id          INTEGER PRIMARY KEY,
  alias       TEXT NOT NULL,
  chat_id     TEXT,
  candidates  TEXT,
  resolved_to TEXT,
  created_at  REAL NOT NULL
);

-- ===========================================================================
-- 五、联想图
-- ===========================================================================

CREATE TABLE IF NOT EXISTS nodes (
  id         INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  kind       TEXT NOT NULL,
  ref        TEXT NOT NULL,
  label      TEXT,
  weight     REAL NOT NULL DEFAULT 1.0,
  created_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_node_ref ON nodes(account_id, kind, ref);
CREATE INDEX IF NOT EXISTS idx_node_kind ON nodes(kind, weight DESC);

CREATE TABLE IF NOT EXISTS edges (
  id         INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  from_id    INTEGER NOT NULL,
  to_id      INTEGER NOT NULL,
  rel        TEXT NOT NULL,
  weight     REAL NOT NULL DEFAULT 0.5,
  evidence   TEXT,
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  FOREIGN KEY (from_id) REFERENCES nodes(id) ON DELETE CASCADE,
  FOREIGN KEY (to_id)   REFERENCES nodes(id) ON DELETE CASCADE
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_edge_uniq ON edges(from_id, to_id, rel);
CREATE INDEX IF NOT EXISTS idx_edge_from ON edges(from_id, weight DESC);
CREATE INDEX IF NOT EXISTS idx_edge_to   ON edges(to_id, weight DESC);
CREATE INDEX IF NOT EXISTS idx_edge_rel  ON edges(rel, weight DESC);

-- ===========================================================================
-- 六、自我参照
-- ===========================================================================

CREATE TABLE IF NOT EXISTS self_profile (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  category    TEXT NOT NULL,
  subject     TEXT NOT NULL,
  value       TEXT NOT NULL,
  confidence  REAL NOT NULL DEFAULT 0.7,
  valid_from  REAL,
  valid_until REAL,
  source_ep   INTEGER,
  created_at  REAL NOT NULL,
  FOREIGN KEY (source_ep) REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS idx_self_cat ON self_profile(account_id, category, valid_until);

CREATE TABLE IF NOT EXISTS self_events (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  episode_id  INTEGER,
  summary     TEXT NOT NULL,
  importance  REAL NOT NULL DEFAULT 0.5,
  created_at  REAL NOT NULL,
  FOREIGN KEY (episode_id) REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS idx_se_time ON self_events(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS feedback (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  from_qq     TEXT NOT NULL,
  about       TEXT,
  text        TEXT NOT NULL,
  sentiment   REAL NOT NULL DEFAULT 0,
  episode_id  INTEGER,
  created_at  REAL NOT NULL,
  FOREIGN KEY (episode_id) REFERENCES episodes(id)
);
CREATE INDEX IF NOT EXISTS idx_fb_from ON feedback(from_qq, created_at DESC);

-- ===========================================================================
-- 七、人格
-- ===========================================================================

CREATE TABLE IF NOT EXISTS persona_cards (
  id          TEXT PRIMARY KEY,
  account_id  TEXT NOT NULL,
  name        TEXT NOT NULL,
  avatar      TEXT,
  alias       TEXT,
  identity    TEXT,
  background  TEXT,
  traits      TEXT,
  speech      TEXT,
  boundaries  TEXT,
  examples    TEXT,
  tags        TEXT,
  version     INTEGER NOT NULL DEFAULT 1,
  updated_at  REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS persona_keywords (
  id            TEXT PRIMARY KEY,
  persona_id    TEXT NOT NULL,
  keywords      TEXT NOT NULL,
  match_mode    TEXT NOT NULL DEFAULT 'any',
  effects       TEXT NOT NULL,
  chance        REAL NOT NULL DEFAULT 1.0,
  cooldown      INTEGER NOT NULL DEFAULT 0,
  priority      INTEGER NOT NULL DEFAULT 0,
  once_per_chat INTEGER NOT NULL DEFAULT 0,
  enabled       INTEGER NOT NULL DEFAULT 1,
  note          TEXT,
  FOREIGN KEY (persona_id) REFERENCES persona_cards(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_pk_persona ON persona_keywords(persona_id, priority DESC);

CREATE TABLE IF NOT EXISTS persona_state (
  persona_id  TEXT NOT NULL,
  chat_id     TEXT NOT NULL,
  mood        REAL NOT NULL DEFAULT 0,
  affection   REAL NOT NULL DEFAULT 0.3,
  closeness   REAL NOT NULL DEFAULT 0.2,
  energy      REAL NOT NULL DEFAULT 0.7,
  patience    REAL NOT NULL DEFAULT 0.6,
  updated_at  REAL NOT NULL,
  PRIMARY KEY (persona_id, chat_id)
);

CREATE TABLE IF NOT EXISTS keyword_fires (
  id       INTEGER PRIMARY KEY,
  rule_id  TEXT NOT NULL,
  chat_id  TEXT NOT NULL,
  fired_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_kf ON keyword_fires(rule_id, chat_id, fired_at DESC);

CREATE TABLE IF NOT EXISTS persona_raw_blocks (
  id          INTEGER PRIMARY KEY,
  persona_id  TEXT NOT NULL,
  title       TEXT NOT NULL,
  kind        TEXT NOT NULL DEFAULT 'other',
  content     TEXT NOT NULL,
  sort_order  INTEGER NOT NULL DEFAULT 0,
  locked      INTEGER NOT NULL DEFAULT 0,
  enabled     INTEGER NOT NULL DEFAULT 1,
  weight      REAL NOT NULL DEFAULT 1.0,
  note        TEXT,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL,
  FOREIGN KEY (persona_id) REFERENCES persona_cards(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_prb_persona ON persona_raw_blocks(persona_id, sort_order);

-- ===========================================================================
-- 八、语音
-- ===========================================================================

CREATE TABLE IF NOT EXISTS voice_refs (
  id          TEXT PRIMARY KEY,
  account_id  TEXT NOT NULL DEFAULT '',
  persona_id  TEXT NOT NULL DEFAULT '',  -- 空串 = 角色内通用参考音
  name        TEXT NOT NULL,
  engine      TEXT NOT NULL DEFAULT 'gptsovits',
  audio_path  TEXT NOT NULL,
  duration    REAL,
  prompt_text TEXT NOT NULL DEFAULT '',
  params      TEXT NOT NULL DEFAULT '{}',
  weight      REAL NOT NULL DEFAULT 1.0,
  tags        TEXT DEFAULT '[]',
  min_chars   INTEGER DEFAULT 0,
  max_chars   INTEGER DEFAULT 9999,
  emotions    TEXT DEFAULT '[]',
  condition   TEXT,
  enabled     INTEGER NOT NULL DEFAULT 1,
  note        TEXT,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vr_persona ON voice_refs(account_id, persona_id, enabled);

-- 语音引擎（面板可配；config 只做首次播种）
CREATE TABLE IF NOT EXISTS voice_engines (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  type        TEXT NOT NULL DEFAULT 'gptsovits',
  url         TEXT NOT NULL DEFAULT '',
  api_key     TEXT NOT NULL DEFAULT '',
  model       TEXT NOT NULL DEFAULT '',
  voice       TEXT NOT NULL DEFAULT '',
  timeout     REAL NOT NULL DEFAULT 120,
  defaults    TEXT NOT NULL DEFAULT '{}',
  enabled     INTEGER NOT NULL DEFAULT 1,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);


CREATE TABLE IF NOT EXISTS voice_logs (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL DEFAULT '',
  persona_id  TEXT,
  ref_id      TEXT,
  engine      TEXT,
  text        TEXT,
  text_len    INTEGER,
  params      TEXT,
  duration    REAL,
  ok          INTEGER,
  error       TEXT,
  created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_vl_time ON voice_logs(created_at DESC);

-- ===========================================================================
-- 九、向量
-- ===========================================================================

CREATE TABLE IF NOT EXISTS vectors (
  id         INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  kind       TEXT NOT NULL,
  ref_id     TEXT NOT NULL,
  model      TEXT NOT NULL,
  dim        INTEGER NOT NULL,
  vec        BLOB NOT NULL,
  updated_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_vec_ref ON vectors(account_id, kind, ref_id, model);
CREATE INDEX IF NOT EXISTS idx_vec_kind ON vectors(kind, model);

-- ===========================================================================
-- 十、运行态
-- ===========================================================================

CREATE TABLE IF NOT EXISTS pending_distill (
  id         INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  chat_id    TEXT NOT NULL,
  ep_ids     TEXT NOT NULL,
  queued_at  REAL NOT NULL,
  attempts   INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);
CREATE INDEX IF NOT EXISTS idx_pd_ready ON pending_distill(queued_at) WHERE attempts < 3;

CREATE TABLE IF NOT EXISTS jobs (
  id          INTEGER PRIMARY KEY,
  kind        TEXT NOT NULL,
  payload     TEXT,
  status      TEXT NOT NULL DEFAULT 'pending',
  attempts    INTEGER NOT NULL DEFAULT 0,
  created_at  REAL NOT NULL,
  started_at  REAL,
  finished_at REAL,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, created_at);

CREATE TABLE IF NOT EXISTS meta (
  k          TEXT PRIMARY KEY,
  v          TEXT,
  updated_at REAL
);

CREATE TABLE IF NOT EXISTS audit (
  id         INTEGER PRIMARY KEY,
  account_id TEXT,
  action     TEXT NOT NULL,
  target     TEXT NOT NULL,
  before     TEXT,
  after      TEXT,
  actor      TEXT,
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_audit_target ON audit(target, created_at DESC);

-- ===========================================================================
-- 十一、画像与别名（新皮层的一部分）
-- ===========================================================================

-- 别名表：名字 → QQ 号。同一个人可能有昵称、群名片、备注、自称多个名字
CREATE TABLE IF NOT EXISTS person_aliases (
  alias      TEXT NOT NULL,
  qq         TEXT NOT NULL,
  account_id TEXT NOT NULL,
  source     TEXT NOT NULL DEFAULT 'nickname',  -- remark / card / nickname / self / inferred
  chat_id    TEXT,                               -- 群名片要区分是哪个群
  weight     REAL NOT NULL DEFAULT 1.0,
  first_seen REAL NOT NULL,
  last_seen  REAL NOT NULL,
  PRIMARY KEY (alias, qq, account_id, chat_id)
);
CREATE INDEX IF NOT EXISTS idx_alias_lookup ON person_aliases(alias, account_id);

-- 人物兴趣（单独一张表，便于衰减）
CREATE TABLE IF NOT EXISTS person_interests (
  qq           TEXT NOT NULL,
  account_id   TEXT NOT NULL,
  topic        TEXT NOT NULL,
  kind         TEXT NOT NULL DEFAULT 'like',   -- like / dislike
  weight       REAL NOT NULL DEFAULT 0.5,
  last_mention REAL,
  evidence     TEXT,                            -- JSON: episode id 数组
  created_at   REAL NOT NULL,
  PRIMARY KEY (qq, account_id, topic, kind)
);
CREATE INDEX IF NOT EXISTS idx_pi_qq ON person_interests(account_id, qq, kind, weight DESC);

-- 画像字段变更历史
CREATE TABLE IF NOT EXISTS profile_history (
  id         INTEGER PRIMARY KEY,
  qq         TEXT NOT NULL,
  account_id TEXT NOT NULL,
  field      TEXT NOT NULL,
  old_value  TEXT,
  new_value  TEXT,
  changed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ph_qq ON profile_history(qq, changed_at DESC);

-- ===========================================================================
-- 十二、演化与遗忘
-- ===========================================================================

-- 趋势：某人在某个维度上的变化方向
CREATE TABLE IF NOT EXISTS trends (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  qq          TEXT NOT NULL,
  dimension   TEXT NOT NULL,      -- mood / activity / social
  direction   TEXT NOT NULL,      -- up / down / stable
  strength    REAL,               -- 变化幅度 0-1
  window_days INTEGER,
  evidence    TEXT,               -- JSON: 支撑的数据点
  computed_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trend_qq ON trends(account_id, qq, dimension, computed_at DESC);

-- 压缩记录：原话 → 摘要 → 事实，记下每次压缩
CREATE TABLE IF NOT EXISTS compressions (
  id         INTEGER PRIMARY KEY,
  account_id TEXT NOT NULL,
  from_kind  TEXT NOT NULL,       -- episode / gist
  from_ids   TEXT NOT NULL,       -- JSON 数组
  to_kind    TEXT NOT NULL,       -- gist / fact
  to_id      INTEGER NOT NULL,
  ratio      REAL,                -- 压缩比
  created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_comp_time ON compressions(account_id, created_at DESC);

-- ===========================================================================
-- 十三、运行设置（界面改的值存这里，不改文件）
-- ===========================================================================

CREATE TABLE IF NOT EXISTS settings (
  account_id TEXT NOT NULL DEFAULT '',
  key        TEXT NOT NULL,
  value      TEXT NOT NULL,       -- JSON
  updated_at REAL NOT NULL,
  PRIMARY KEY (account_id, key)
);
CREATE INDEX IF NOT EXISTS idx_set_key ON settings(key);

-- ===========================================================================
-- 十四、视图
-- ===========================================================================

CREATE VIEW IF NOT EXISTS v_live_facts AS
SELECT * FROM facts
WHERE gone = 0 AND (valid_until IS NULL OR valid_until > (strftime('%s','now') + 0));

CREATE VIEW IF NOT EXISTS v_active_today AS
SELECT sender_qq, COUNT(*) AS n, MAX(event_at) AS last_at
FROM episodes
WHERE gone = 0 AND event_at > (strftime('%s','now') + 0) - 86400
GROUP BY sender_qq
ORDER BY n DESC;

-- ===========================================================================
-- 主动私聊
-- ===========================================================================

CREATE TABLE IF NOT EXISTS proactive_state (
  account_id      TEXT NOT NULL,
  person_qq       TEXT NOT NULL,
  last_sent_at    REAL,
  last_reply_at   REAL,
  ignored_streak  INTEGER NOT NULL DEFAULT 0,
  next_eligible_at REAL,
  daily_date      TEXT,
  daily_count     INTEGER NOT NULL DEFAULT 0,
  last_score      REAL,
  last_status     TEXT,
  updated_at      REAL NOT NULL,
  PRIMARY KEY (account_id, person_qq)
);

CREATE TABLE IF NOT EXISTS proactive_log (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  person_qq   TEXT NOT NULL,
  person_name TEXT,
  sent_at     REAL NOT NULL,
  message     TEXT NOT NULL,
  topic       TEXT,
  score       REAL,
  reason      TEXT,
  replied_at  REAL,
  reply_text  TEXT,
  status      TEXT NOT NULL DEFAULT 'sent',
  error       TEXT,
  created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proactive_person ON proactive_log(account_id, person_qq, sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_proactive_sent   ON proactive_log(sent_at DESC);
CREATE INDEX IF NOT EXISTS idx_ep_sender_time   ON episodes(account_id, sender_qq, event_at DESC);

-- ===========================================================================
-- 工作向：笔记 / 提醒 / 工具审计
-- ===========================================================================

CREATE TABLE IF NOT EXISTS notes (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  person_qq   TEXT NOT NULL,
  chat_id     TEXT,
  title       TEXT,
  body        TEXT NOT NULL,
  tags        TEXT DEFAULT '[]',
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notes_account ON notes(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS reminders (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  person_qq   TEXT NOT NULL,
  chat_id     TEXT NOT NULL,
  text        TEXT NOT NULL,
  due_at      REAL NOT NULL,
  status      TEXT NOT NULL DEFAULT 'pending',   -- pending / sent / failed
  attempts    INTEGER NOT NULL DEFAULT 0,
  created_at  REAL NOT NULL,
  sent_at     REAL,
  error       TEXT
);
CREATE INDEX IF NOT EXISTS idx_reminders_due ON reminders(status, due_at);
CREATE INDEX IF NOT EXISTS idx_reminders_person ON reminders(account_id, person_qq, created_at DESC);

CREATE TABLE IF NOT EXISTS tool_calls (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL,
  chat_id     TEXT,
  person_qq   TEXT,
  tool        TEXT NOT NULL,
  args        TEXT,
  result      TEXT,
  ok          INTEGER NOT NULL DEFAULT 1,
  duration_ms INTEGER NOT NULL DEFAULT 0,
  created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tool_calls_time ON tool_calls(created_at DESC);

-- ===========================================================================
-- 文档知识库（工作向）
-- ===========================================================================

CREATE TABLE IF NOT EXISTS documents (
  id          INTEGER PRIMARY KEY,
  account_id  TEXT NOT NULL DEFAULT '',
  title       TEXT NOT NULL,
  source      TEXT DEFAULT '',
  text        TEXT NOT NULL,
  created_at  REAL NOT NULL,
  updated_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_docs_account ON documents(account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS doc_chunks (
  id          INTEGER PRIMARY KEY,
  document_id INTEGER NOT NULL,
  chunk_index INTEGER NOT NULL,
  text        TEXT NOT NULL,
  line_start  INTEGER DEFAULT 0,
  line_end    INTEGER DEFAULT 0,
  created_at  REAL NOT NULL,
  FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_chunks_doc ON doc_chunks(document_id, chunk_index);


-- 外部 agent 平台的会话映射（DSH / TRAE / CodeBuddy）
-- ===========================================================================
-- 生活模块：模拟作息与生活事件
-- ===========================================================================

CREATE TABLE IF NOT EXISTS life_days (
  account_id TEXT NOT NULL,
  day        TEXT NOT NULL,
  plan_json  TEXT NOT NULL,
  summary    TEXT NOT NULL DEFAULT '',
  created_at REAL NOT NULL,
  updated_at REAL NOT NULL,
  PRIMARY KEY(account_id, day)
);

CREATE TABLE IF NOT EXISTS life_events (
  id         INTEGER PRIMARY KEY AUTOINCREMENT,
  account_id TEXT NOT NULL,
  day        TEXT NOT NULL,
  at         REAL NOT NULL,
  phase      TEXT NOT NULL,
  activity   TEXT NOT NULL,
  kind       TEXT NOT NULL DEFAULT 'phase',
  created_at REAL NOT NULL,
  UNIQUE(account_id, day, phase)
);
CREATE INDEX IF NOT EXISTS idx_life_events_day ON life_events(account_id, day, at);

CREATE TABLE IF NOT EXISTS platform_sessions (
  platform    TEXT NOT NULL,
  external_id TEXT NOT NULL,
  chat_id     TEXT NOT NULL,
  account_id  TEXT NOT NULL DEFAULT '',
  title       TEXT,
  created_at  REAL NOT NULL,
  last_active REAL NOT NULL,
  PRIMARY KEY (platform, external_id)
);
CREATE INDEX IF NOT EXISTS idx_ps_chat ON platform_sessions(chat_id);
