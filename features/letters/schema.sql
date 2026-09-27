-- Letter status tracker + in-app notifications. Owned by features/letters.
CREATE TABLE IF NOT EXISTS letters_status (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  tournament_id INTEGER NOT NULL,
  stage TEXT NOT NULL DEFAULT 'drafted',      -- drafted|ped_signed|proctor_signed|hod_approved|submitted
  needs_redo INTEGER NOT NULL DEFAULT 0,      -- 1 when tournament dates moved after drafting
  rejected INTEGER NOT NULL DEFAULT 0,        -- 1 when a signer rejected it; reason is in letters_history
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (athlete_id, tournament_id));
CREATE TABLE IF NOT EXISTS letters_history (
  id INTEGER PRIMARY KEY,
  letter_id INTEGER NOT NULL,
  stage TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  changed_by TEXT,
  note TEXT);
CREATE TABLE IF NOT EXISTS letters_notifications (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  tournament_id INTEGER,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL,
  read_at TEXT);
CREATE INDEX IF NOT EXISTS idx_letters_notif_athlete ON letters_notifications(athlete_id, read_at);
