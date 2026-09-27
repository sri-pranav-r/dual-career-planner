-- Owned by the verification feature. Core runs this on connect.

-- One signing key per database, created on first use. Tokens in letter QR
-- codes are HMAC-signed with it, so a letter cannot be forged or edited
-- without the link failing verification.
CREATE TABLE IF NOT EXISTS verification_keys (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  secret TEXT NOT NULL,
  created_at TEXT NOT NULL
);

-- Every approve / reject a faculty member makes on a letter. The latest row
-- per (athlete, tournament, reviewer_title) is the current decision.
CREATE TABLE IF NOT EXISTS verification_decisions (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  tournament_id INTEGER NOT NULL,
  reviewer_id INTEGER,
  reviewer_name TEXT,
  reviewer_title TEXT,          -- 'proctor' | 'teacher' | 'hod' | 'admin'
  decision TEXT NOT NULL,       -- 'approved' | 'rejected'
  note TEXT,
  decided_at TEXT NOT NULL      -- ISO timestamp
);
CREATE INDEX IF NOT EXISTS verification_decisions_letter
  ON verification_decisions (athlete_id, tournament_id);
