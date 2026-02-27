PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE
);

-- Drafts keyed by user_id (one draft per user in this prototype)
CREATE TABLE IF NOT EXISTS ticket_drafts (
  user_id INTEGER PRIMARY KEY,
  ai_turns INTEGER DEFAULT 0,
  state TEXT NOT NULL CHECK (state IN ('draft','submitted','abandoned')),
  draft_title TEXT,
  draft_description TEXT,
  ai_questions_json TEXT,
  ai_answers_json TEXT,
  started_at INTEGER,
  submitted_at INTEGER,
  log_table INTEGER,
  FOREIGN KEY (user_id) REFERENCES users(id)
);

-- Five separate ticket tables. The application routes inserts to tickets_1..tickets_5
CREATE TABLE IF NOT EXISTS tickets_1 (
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  time_to_submit_ms INTEGER,
  ai_used INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open',
  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS tickets_2 (
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  time_to_submit_ms INTEGER,
  ai_used INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open',
  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS tickets_3 (
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  time_to_submit_ms INTEGER,
  ai_used INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open',
  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS tickets_4 (
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  time_to_submit_ms INTEGER,
  ai_used INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open',
  FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS tickets_5 (
  user_id INTEGER NOT NULL,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  time_to_submit_ms INTEGER,
  ai_used INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open',
  FOREIGN KEY (user_id) REFERENCES users(id)
);
