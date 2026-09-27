-- Reminders, nudges and weekly team load reports. Owned by features/reminders.
CREATE TABLE IF NOT EXISTS reminders_outbox (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER,
  kind TEXT NOT NULL,              -- tournament | nudge
  dedupe_key TEXT NOT NULL,        -- one message per key and channel, so re-running never double-sends
  channel TEXT NOT NULL,           -- in_app | whatsapp | sms
  to_addr TEXT,
  message TEXT NOT NULL,
  status TEXT NOT NULL,            -- sent | dry_run | failed | skipped
  detail TEXT,
  created_at TEXT NOT NULL,
  UNIQUE (dedupe_key, channel));
CREATE INDEX IF NOT EXISTS idx_reminders_outbox_athlete ON reminders_outbox(athlete_id);
CREATE TABLE IF NOT EXISTS reminders_prefs (
  athlete_id INTEGER PRIMARY KEY,
  external_ok INTEGER NOT NULL DEFAULT 0,     -- athlete consented to WhatsApp/SMS
  channel TEXT NOT NULL DEFAULT 'whatsapp',   -- whatsapp | sms
  updated_at TEXT);
CREATE TABLE IF NOT EXISTS reminders_reports (
  id INTEGER PRIMARY KEY,
  sport TEXT NOT NULL,
  week_end TEXT NOT NULL,
  created_at TEXT NOT NULL,
  summary TEXT NOT NULL,
  rows_json TEXT NOT NULL,
  UNIQUE (sport, week_end));
CREATE TABLE IF NOT EXISTS reminders_runs (
  day TEXT PRIMARY KEY,
  ran_at TEXT NOT NULL,
  summary TEXT);
