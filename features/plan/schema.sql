-- Owned by features/plan: the "Next 14 days" suggestions each athlete was shown, and whether they followed them.
-- One row per athlete, day and area ('training' | 'study' | 'academic'). Rows for past days are frozen, so the
-- pilot keeps what the athlete actually saw even if the rules or the data change later.
CREATE TABLE IF NOT EXISTS plan_suggestions (
  id INTEGER PRIMARY KEY,
  athlete_id INTEGER NOT NULL,
  day TEXT NOT NULL,                 -- ISO date the suggestion is for
  area TEXT NOT NULL,
  rule_id TEXT NOT NULL,             -- e.g. 'P1', see features/plan/logic.py RULES
  level TEXT,                        -- training intensity: rest|recovery|light|easy|moderate|compete
  text TEXT NOT NULL,
  first_shown TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE (athlete_id, day, area));
CREATE INDEX IF NOT EXISTS plan_suggestions_athlete ON plan_suggestions(athlete_id, day);

-- The athlete's answer to "Did you follow it?". One per suggestion; answering again replaces it.
CREATE TABLE IF NOT EXISTS plan_followups (
  id INTEGER PRIMARY KEY,
  suggestion_id INTEGER NOT NULL UNIQUE,
  athlete_id INTEGER NOT NULL,
  status TEXT NOT NULL,              -- followed | partly | not_followed
  reason TEXT,                       -- why not (only for partly / not_followed)
  note TEXT,
  logged_at TEXT NOT NULL,
  logged_by TEXT);
CREATE INDEX IF NOT EXISTS plan_followups_athlete ON plan_followups(athlete_id);
