"""SQLite schema migrations for the standard football match repository."""

SCHEMA_VERSION = 3


SCHEMA_V1 = """
CREATE TABLE teams (
    team_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    team_type TEXT NOT NULL CHECK (team_type IN ('national', 'club')),
    country_code TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (canonical_name, team_type)
);

CREATE TABLE team_aliases (
    alias_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    raw_alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    team_type TEXT NOT NULL CHECK (team_type IN ('national', 'club')),
    team_id TEXT NOT NULL REFERENCES teams(team_id),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (source, normalized_alias, team_type)
);

CREATE TABLE competitions (
    competition_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    competition_type TEXT NOT NULL DEFAULT 'unknown',
    country_code TEXT,
    level INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE matches (
    match_id TEXT PRIMARY KEY,
    competition_id TEXT NOT NULL REFERENCES competitions(competition_id),
    season TEXT,
    stage TEXT,
    event_date TEXT NOT NULL,
    kickoff_utc TEXT,
    source_timezone TEXT,
    original_time TEXT,
    time_precision TEXT NOT NULL CHECK (time_precision IN ('date', 'minute')),
    home_team_id TEXT NOT NULL REFERENCES teams(team_id),
    away_team_id TEXT NOT NULL REFERENCES teams(team_id),
    neutral INTEGER NOT NULL DEFAULT 0 CHECK (neutral IN (0, 1)),
    status TEXT NOT NULL CHECK (status IN ('scheduled', 'finished', 'postponed', 'cancelled')),
    home_goals INTEGER CHECK (home_goals IS NULL OR home_goals >= 0),
    away_goals INTEGER CHECK (away_goals IS NULL OR away_goals >= 0),
    data_quality_status TEXT NOT NULL DEFAULT 'valid',
    identity_fingerprint TEXT NOT NULL,
    content_fingerprint TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK (home_team_id <> away_team_id),
    CHECK (
        status <> 'finished'
        OR (home_goals IS NOT NULL AND away_goals IS NOT NULL)
    )
);
CREATE INDEX idx_matches_event_date ON matches(event_date);
CREATE INDEX idx_matches_kickoff ON matches(kickoff_utc);
CREATE INDEX idx_matches_competition ON matches(competition_id, event_date);
CREATE INDEX idx_matches_identity ON matches(identity_fingerprint);

CREATE TABLE source_records (
    source_record_pk TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    source_record_id TEXT,
    identity_fingerprint TEXT NOT NULL,
    raw_payload_json TEXT NOT NULL,
    payload_fingerprint TEXT NOT NULL,
    fetched_at TEXT NOT NULL,
    source_revision_at TEXT,
    parse_status TEXT NOT NULL,
    error_summary TEXT,
    created_at TEXT NOT NULL
);
CREATE UNIQUE INDEX uq_source_records_with_id
    ON source_records(source, source_record_id, payload_fingerprint)
    WHERE source_record_id IS NOT NULL;
CREATE UNIQUE INDEX uq_source_records_without_id
    ON source_records(source, identity_fingerprint, payload_fingerprint)
    WHERE source_record_id IS NULL;

CREATE TABLE match_sources (
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    source_record_pk TEXT NOT NULL UNIQUE REFERENCES source_records(source_record_pk) ON DELETE CASCADE,
    linked_at TEXT NOT NULL,
    PRIMARY KEY (match_id, source_record_pk)
);

CREATE TABLE sync_runs (
    sync_run_id TEXT PRIMARY KEY,
    sync_type TEXT NOT NULL,
    scope_json TEXT NOT NULL DEFAULT '{}',
    started_at TEXT NOT NULL,
    finished_at TEXT,
    fetched_count INTEGER NOT NULL DEFAULT 0,
    inserted_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    skipped_count INTEGER NOT NULL DEFAULT 0,
    rejected_count INTEGER NOT NULL DEFAULT 0,
    unmatched_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    error_summary TEXT
);

CREATE TABLE unmatched_team_aliases (
    unmatched_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source TEXT NOT NULL,
    raw_alias TEXT NOT NULL,
    normalized_alias TEXT NOT NULL,
    team_type TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT 'pending',
    UNIQUE (source, normalized_alias, team_type)
);

CREATE TABLE duplicate_candidates (
    candidate_id TEXT PRIMARY KEY,
    match_id_a TEXT NOT NULL REFERENCES matches(match_id),
    match_id_b TEXT NOT NULL REFERENCES matches(match_id),
    reason TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at TEXT NOT NULL,
    UNIQUE (match_id_a, match_id_b, reason)
);

CREATE TABLE odds_snapshots (
    odds_snapshot_id TEXT PRIMARY KEY,
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    company TEXT NOT NULL,
    captured_at TEXT NOT NULL,
    home_odds REAL NOT NULL CHECK (home_odds > 1),
    draw_odds REAL NOT NULL CHECK (draw_odds > 1),
    away_odds REAL NOT NULL CHECK (away_odds > 1),
    source TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (match_id, company, captured_at)
);
"""


SCHEMA_V2 = """
CREATE TABLE dataset_batches (
    batch_id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    manifest_fingerprint TEXT NOT NULL UNIQUE,
    fetched_at TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE historical_closing_odds (
    closing_odds_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL REFERENCES dataset_batches(batch_id) ON DELETE CASCADE,
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    source_record_pk TEXT NOT NULL REFERENCES source_records(source_record_pk) ON DELETE CASCADE,
    company TEXT NOT NULL,
    home_odds REAL NOT NULL CHECK (home_odds > 1),
    draw_odds REAL NOT NULL CHECK (draw_odds > 1),
    away_odds REAL NOT NULL CHECK (away_odds > 1),
    evidence_type TEXT NOT NULL CHECK (evidence_type = 'source_declared_closing'),
    observed_at TEXT NOT NULL,
    source_file_sha256 TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE (batch_id, source_record_pk, company)
);
CREATE INDEX idx_closing_odds_match_batch
    ON historical_closing_odds(match_id, batch_id, company);
"""


SCHEMA_V3 = """
ALTER TABLE dataset_batches ADD COLUMN membership_status TEXT NOT NULL DEFAULT 'membership_incomplete';
ALTER TABLE dataset_batches ADD COLUMN member_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE dataset_batches ADD COLUMN expected_member_count INTEGER NOT NULL DEFAULT 0;

CREATE TABLE dataset_batch_matches (
    batch_id TEXT NOT NULL REFERENCES dataset_batches(batch_id) ON DELETE CASCADE,
    source_record_pk TEXT NOT NULL REFERENCES source_records(source_record_pk) ON DELETE CASCADE,
    match_id TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
    PRIMARY KEY (batch_id, source_record_pk)
);
CREATE INDEX idx_dataset_batch_matches_batch_match
    ON dataset_batch_matches(batch_id, match_id);

INSERT OR IGNORE INTO dataset_batch_matches(batch_id, source_record_pk, match_id)
SELECT DISTINCT batch_id, source_record_pk, match_id
FROM historical_closing_odds;

UPDATE dataset_batches
SET member_count = (
    SELECT COUNT(*) FROM dataset_batch_matches dbm
    WHERE dbm.batch_id = dataset_batches.batch_id
), expected_member_count = (
    SELECT COUNT(*) FROM dataset_batch_matches dbm
    WHERE dbm.batch_id = dataset_batches.batch_id
), membership_status = 'membership_incomplete';
"""


def _migrate_v1_to_v2(connection):
    connection.executescript(SCHEMA_V2)
    connection.execute("PRAGMA user_version = 2")


def _migrate_v2_to_v3(connection):
    connection.executescript(SCHEMA_V3)
    connection.execute("PRAGMA user_version = 3")


def apply_migrations(connection):
    """Initialize an empty database or migrate it to the supported schema."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    if version == SCHEMA_VERSION:
        return
    if version == 2:
        _migrate_v2_to_v3(connection)
        return
    if version == 1:
        _migrate_v1_to_v2(connection)
        _migrate_v2_to_v3(connection)
        return
    if version != 0:
        raise RuntimeError(
            f"unsupported database schema version: {version}; expected {SCHEMA_VERSION}"
        )

    existing = connection.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
    ).fetchall()
    if existing:
        raise RuntimeError("database has tables but no supported schema version")

    connection.executescript(SCHEMA_V1)
    connection.execute("PRAGMA user_version = 1")
    _migrate_v1_to_v2(connection)
    _migrate_v2_to_v3(connection)
