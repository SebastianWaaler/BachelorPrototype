PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS users (
  id INTEGER PRIMARY KEY,
  username TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS ticket_drafts (
  id TEXT PRIMARY KEY,         -- UUID
  user_id INTEGER NOT NULL,
  started_at INTEGER NOT NULL, -- unix ms
  last_activity_at INTEGER NOT NULL,
  ai_turns INTEGER DEFAULT 0,
  state TEXT NOT NULL CHECK (state IN ('draft','submitted','abandoned')),
  FOREIGN KEY (user_id) REFERENCES users(id)
);

CREATE TABLE IF NOT EXISTS tickets (
  id TEXT PRIMARY KEY,         -- UUID
  user_id INTEGER NOT NULL,
  draft_id TEXT,
  title TEXT NOT NULL,
  description TEXT NOT NULL,
  created_at INTEGER NOT NULL, -- unix ms
  time_to_submit_ms INTEGER,
  ai_used INTEGER DEFAULT 0,
  status TEXT DEFAULT 'open',
  FOREIGN KEY (user_id) REFERENCES users(id),
  FOREIGN KEY (draft_id) REFERENCES ticket_drafts(id)
);
