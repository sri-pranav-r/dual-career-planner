-- imports feature: class-level copies of imported timetables and exam calendars.
-- section: '*' = every section of that dept + sem, '' = no section, else the section.
CREATE TABLE IF NOT EXISTS imports_class_timetable (
  id INTEGER PRIMARY KEY, dept TEXT, sem TEXT, section TEXT,
  weekday INTEGER, subject TEXT, kind TEXT);
CREATE TABLE IF NOT EXISTS imports_class_events (
  id INTEGER PRIMARY KEY, dept TEXT, sem TEXT, section TEXT,
  date TEXT, kind TEXT, title TEXT,
  UNIQUE (dept, sem, section, date, kind, title));
CREATE TABLE IF NOT EXISTS imports_log (
  id INTEGER PRIMARY KEY, kind TEXT, dept TEXT, sem TEXT, section TEXT,
  source_name TEXT, rows INTEGER, athletes INTEGER, imported_by INTEGER, imported_at TEXT,
  athlete_id INTEGER);  -- set for an athlete's own upload, so data deletion can remove it
