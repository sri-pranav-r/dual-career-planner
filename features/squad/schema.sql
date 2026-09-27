-- Make-up test requests (one per missed CIE / SEE). Owned by features/squad.
CREATE TABLE IF NOT EXISTS squad_makeup_requests (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  tournament_id INTEGER NOT NULL,
  test_date TEXT NOT NULL,                    -- ISO date of the CIE / SEE missed
  kind TEXT NOT NULL,                         -- 'CIE' | 'SEE'
  title TEXT NOT NULL,                        -- e.g. "Data Structures CIE-1"
  status TEXT NOT NULL DEFAULT 'requested',   -- requested|approved|rejected|completed|withdrawn
  makeup_date TEXT,                           -- set on approval
  makeup_note TEXT,                           -- time / room / anything the faculty adds
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (athlete_id, test_date, kind, title));
CREATE TABLE IF NOT EXISTS squad_makeup_history (
  id INTEGER PRIMARY KEY,
  request_id INTEGER NOT NULL,
  status TEXT NOT NULL,
  changed_at TEXT NOT NULL,
  changed_by TEXT,
  note TEXT);
CREATE INDEX IF NOT EXISTS idx_squad_makeup_athlete ON squad_makeup_requests(athlete_id, status);
