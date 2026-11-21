-- UFO evidence board: claims-not-columns schema.
-- Every fact is a claim tied to a verbatim quote in a stored source.

CREATE TABLE IF NOT EXISTS source (
  id          INTEGER PRIMARY KEY,
  slug        TEXT UNIQUE NOT NULL,
  title       TEXT NOT NULL,
  url         TEXT NOT NULL,
  revision_id INTEGER,                  -- exact version the quotes come from
  fetched_at  TEXT,
  license     TEXT,
  lat REAL, lon REAL,
  kind        TEXT,                     -- encyclopedia | hearing | government report | case resolution
  publisher   TEXT,
  date        TEXT,                     -- publication or event date of the document
  case_slug   TEXT NOT NULL,            -- the case file this source belongs to (several sources can share one)
  case_name   TEXT,                     -- name of the case file when no encyclopedia source names it
  case_kind   TEXT NOT NULL DEFAULT 'case',   -- case | document (a hearing or report that spans many cases)
  text        TEXT NOT NULL
);

-- Entities exactly as extracted from one source ("raw"). Resolution never
-- edits these; it only fills canonical_id, so it can be re-run at any time.
CREATE TABLE IF NOT EXISTS entity (
  id           INTEGER PRIMARY KEY,
  source_id    INTEGER NOT NULL REFERENCES source(id),
  key          TEXT NOT NULL,           -- local key inside the extraction file
  type         TEXT NOT NULL,           -- case|person|organization|place|craft|vehicle|document|program|work|feature|explanation
  name         TEXT NOT NULL,
  description  TEXT,
  canonical_id INTEGER REFERENCES canonical(id),
  UNIQUE (source_id, key)
);

CREATE TABLE IF NOT EXISTS alias (
  entity_id INTEGER NOT NULL REFERENCES entity(id),
  alias     TEXT NOT NULL
);

-- One row per real-world thing after merging raw entities across sources.
CREATE TABLE IF NOT EXISTS canonical (
  id   INTEGER PRIMARY KEY,
  type TEXT NOT NULL,
  name TEXT NOT NULL,
  n_cases INTEGER DEFAULT 0             -- how many cases mention it (rarity)
);

-- subject --predicate--> object (another entity) OR value (a literal).
CREATE TABLE IF NOT EXISTS claim (
  id          INTEGER PRIMARY KEY,
  source_id   INTEGER NOT NULL REFERENCES source(id),
  subject_id  INTEGER NOT NULL REFERENCES entity(id),
  predicate   TEXT NOT NULL,
  object_id   INTEGER REFERENCES entity(id),
  value       TEXT,
  quote       TEXT NOT NULL,            -- verbatim from source.text
  quote_start INTEGER NOT NULL,         -- char offset into source.text
  quote_end   INTEGER NOT NULL,
  CHECK ((object_id IS NULL) <> (value IS NULL))
);

CREATE INDEX IF NOT EXISTS claim_subject ON claim(subject_id);
CREATE INDEX IF NOT EXISTS claim_object  ON claim(object_id);
CREATE INDEX IF NOT EXISTS entity_canon  ON entity(canonical_id);
