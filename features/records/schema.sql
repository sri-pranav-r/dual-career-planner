-- Owned by features/records: injury log, consent, deletion requests.
CREATE TABLE IF NOT EXISTS records_injuries (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  injured_on TEXT NOT NULL,          -- ISO date
  body_part TEXT NOT NULL,
  what TEXT NOT NULL,                -- short description: "hamstring strain in sprint drill"
  days_out INTEGER NOT NULL DEFAULT 0,
  logged_by TEXT,
  created_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS records_injuries_athlete ON records_injuries(athlete_id, injured_on);

-- Latest row per athlete is their current answer to the consent notice.
CREATE TABLE IF NOT EXISTS records_consent (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  version INTEGER NOT NULL,
  accepted INTEGER NOT NULL,         -- 1 = agreed, 0 = withdrew
  decided_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS records_consent_athlete ON records_consent(athlete_id, id);

-- Athlete asks, admin carries it out. Once done the row keeps no name or USN.
CREATE TABLE IF NOT EXISTS records_deletion_requests (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  reason TEXT,
  status TEXT NOT NULL DEFAULT 'pending',   -- pending | done | cancelled
  requested_at TEXT NOT NULL,
  handled_by TEXT,
  handled_at TEXT);
