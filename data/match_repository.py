"""Transactional SQLite repository for canonical football match data."""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import unicodedata
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from config import LEAGUES, LEAGUE_NAMES_CN
from data.migrations import SCHEMA_VERSION, apply_migrations
from data.source_adapters import SourceRecord, adapt_source_payload


DATA_DIR = Path(__file__).resolve().parent
DEFAULT_DATABASE_PATH = DATA_DIR / "processed" / "football.db"
DEFAULT_ALIASES_PATH = DATA_DIR / "reference" / "team_aliases.json"
ID_NAMESPACE = uuid.UUID("cae5eaa2-f973-4c94-9020-fc7865aa5f7d")


class RepositoryError(RuntimeError):
    pass


class RepositoryNotInitializedError(RepositoryError):
    pass


class RepositorySchemaError(RepositoryError):
    pass


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def fingerprint(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def normalize_alias(value: str) -> str:
    value = unicodedata.normalize("NFC", str(value or "")).strip()
    value = re.sub(r"\s+", " ", value)
    return value.casefold()


def stable_id(kind: str, value: str) -> str:
    return str(uuid.uuid5(ID_NAMESPACE, f"{kind}:{value}"))


def _default_database_path() -> str:
    return os.environ.get("FOOTBALL_DB_PATH", str(DEFAULT_DATABASE_PATH))


def _competition_id(name: str) -> str:
    if name in LEAGUES:
        return LEAGUES[name]
    normalized = normalize_alias(name)
    return f"competition-{hashlib.sha256(normalized.encode('utf-8')).hexdigest()[:16]}"


def _competition_type(name: str) -> str:
    if any(token in name for token in ("杯", "Cup", "Championship", "欧冠", "欧联")):
        return "cup"
    if any(token in name for token in ("预赛", "Qualifier")):
        return "qualifier"
    if any(token in name for token in ("友谊", "Friendly")):
        return "friendly"
    if name and name != "unknown":
        return "league"
    return "unknown"


def normalize_timestamp(value: str) -> str:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


class MatchRepository:
    def __init__(self, database_path: str | os.PathLike[str] | None = None, aliases_path=None):
        self.database_path = str(database_path or _default_database_path())
        self.aliases_path = Path(aliases_path or DEFAULT_ALIASES_PATH)
        self._memory_connection = None

    def close(self):
        if self._memory_connection is not None:
            self._memory_connection.close()
            self._memory_connection = None

    def _new_connection(self):
        connection = sqlite3.connect(self.database_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def _connect(self, *, create=False):
        if self.database_path == ":memory:":
            if self._memory_connection is None:
                if not create:
                    raise RepositoryNotInitializedError("比赛数据库尚未初始化")
                self._memory_connection = self._new_connection()
            return self._memory_connection

        path = Path(self.database_path)
        if not path.exists() and not create:
            raise RepositoryNotInitializedError(f"比赛数据库尚未初始化: {path}")
        if create:
            path.parent.mkdir(parents=True, exist_ok=True)
        return self._new_connection()

    def _validate_schema(self, connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SCHEMA_VERSION:
            raise RepositorySchemaError(
                f"比赛数据库 schema 版本不兼容: {version}, 需要 {SCHEMA_VERSION}"
            )
        required = {"teams", "team_aliases", "matches", "source_records", "sync_runs"}
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        missing = required - tables
        if missing:
            raise RepositorySchemaError(f"比赛数据库缺少表: {', '.join(sorted(missing))}")

    def initialize(self):
        connection = self._connect(create=True)
        try:
            with connection:
                apply_migrations(connection)
                self._seed_reference_data(connection)
            self._validate_schema(connection)
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def _connection(self):
        connection = self._connect()
        try:
            self._validate_schema(connection)
        except Exception:
            if self.database_path != ":memory:":
                connection.close()
            raise
        return connection

    def _seed_reference_data(self, connection):
        if not self.aliases_path.exists():
            raise RepositoryError(f"球队别名种子不存在: {self.aliases_path}")
        with self.aliases_path.open("r", encoding="utf-8") as handle:
            catalog = json.load(handle)
        if catalog.get("schema_version") != 1:
            raise RepositoryError("球队别名种子 schema_version 必须为 1")

        now = utc_now_iso()
        for team_type in ("national", "club"):
            for canonical_name in catalog.get("canonical_teams", {}).get(team_type, []):
                team_id = stable_id("team", f"{team_type}:{canonical_name}")
                connection.execute(
                    """
                    INSERT INTO teams(team_id, canonical_name, team_type, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(team_id) DO UPDATE SET
                        canonical_name=excluded.canonical_name,
                        updated_at=excluded.updated_at
                    """,
                    (team_id, canonical_name, team_type, now, now),
                )
                self._upsert_alias(connection, "*", canonical_name, team_type, team_id, now)

        for entry in catalog.get("aliases", []):
            canonical_name = entry["canonical_name"]
            team_type = entry["team_type"]
            row = connection.execute(
                "SELECT team_id FROM teams WHERE canonical_name=? AND team_type=?",
                (canonical_name, team_type),
            ).fetchone()
            if row is None:
                team_id = stable_id("team", f"{team_type}:{canonical_name}")
                connection.execute(
                    "INSERT INTO teams VALUES (?, ?, ?, NULL, ?, ?)",
                    (team_id, canonical_name, team_type, now, now),
                )
                self._upsert_alias(connection, "*", canonical_name, team_type, team_id, now)
            else:
                team_id = row["team_id"]
            for source in entry.get("sources", ["*"]):
                for alias in entry.get("values", []):
                    self._upsert_alias(connection, source, alias, team_type, team_id, now)

    @staticmethod
    def _upsert_alias(connection, source, raw_alias, team_type, team_id, now):
        connection.execute(
            """
            INSERT INTO team_aliases(
                source, raw_alias, normalized_alias, team_type, team_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source, normalized_alias, team_type) DO UPDATE SET
                raw_alias=excluded.raw_alias,
                team_id=excluded.team_id,
                updated_at=excluded.updated_at
            """,
            (source, raw_alias, normalize_alias(raw_alias), team_type, team_id, now, now),
        )

    def create_sync_run(self, sync_type: str, scope: Mapping[str, Any] | None = None) -> str:
        connection = self._connection()
        sync_run_id = stable_id("sync-run", f"{sync_type}:{uuid.uuid4()}")
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO sync_runs(sync_run_id, sync_type, scope_json, started_at, status)
                    VALUES (?, ?, ?, ?, 'running')
                    """,
                    (sync_run_id, sync_type, canonical_json(scope or {}), utc_now_iso()),
                )
            return sync_run_id
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def import_source_records(
        self,
        records: Iterable[SourceRecord],
        sync_run_id: str,
        *,
        sync_type: str = "import",
    ) -> dict[str, int]:
        connection = self._connection()
        counts = {key: 0 for key in ("fetched", "inserted", "updated", "skipped", "rejected", "unmatched")}
        now = utc_now_iso()
        try:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                INSERT INTO sync_runs(sync_run_id, sync_type, scope_json, started_at, status)
                VALUES (?, ?, '{}', ?, 'running')
                ON CONFLICT(sync_run_id) DO NOTHING
                """,
                (sync_run_id, sync_type, now),
            )
            for record in records:
                if not isinstance(record, SourceRecord):
                    raise TypeError("import_source_records only accepts SourceRecord values")
                counts["fetched"] += 1
                outcome = self._import_one(connection, record, now)
                counts[outcome] += 1
            connection.execute(
                """
                UPDATE sync_runs SET
                    finished_at=?, fetched_count=?, inserted_count=?, updated_count=?,
                    skipped_count=?, rejected_count=?, unmatched_count=?, status='completed',
                    error_summary=NULL
                WHERE sync_run_id=?
                """,
                (
                    utc_now_iso(), counts["fetched"], counts["inserted"], counts["updated"],
                    counts["skipped"], counts["rejected"], counts["unmatched"], sync_run_id,
                ),
            )
            connection.commit()
            return counts
        except Exception as exc:
            connection.rollback()
            with connection:
                connection.execute(
                    """
                    INSERT INTO sync_runs(sync_run_id, sync_type, scope_json, started_at, finished_at, status, error_summary)
                    VALUES (?, ?, '{}', ?, ?, 'failed', ?)
                    ON CONFLICT(sync_run_id) DO UPDATE SET
                        finished_at=excluded.finished_at,
                        status='failed',
                        error_summary=excluded.error_summary
                    """,
                    (sync_run_id, sync_type, now, utc_now_iso(), str(exc)[:500]),
                )
            raise
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def _import_one(self, connection, record: SourceRecord, now: str) -> str:
        raw_json = canonical_json(record.raw_payload)
        payload_fingerprint = fingerprint(record.raw_payload)
        raw_identity = fingerprint({
            "competition": record.competition,
            "season": record.season,
            "home": normalize_alias(record.home_team_raw),
            "away": normalize_alias(record.away_team_raw),
            "event_date": record.event_date,
        })
        source_pk = stable_id(
            "source-record",
            f"{record.source}:{record.source_record_id or raw_identity}:{payload_fingerprint}",
        )
        existing_source = connection.execute(
            "SELECT parse_status FROM source_records WHERE source_record_pk=?",
            (source_pk,),
        ).fetchone()
        if existing_source is not None and existing_source["parse_status"] != "unmatched":
            return "skipped"
        if existing_source is None:
            connection.execute(
                """
                INSERT INTO source_records(
                    source_record_pk, source, source_record_id, identity_fingerprint,
                    raw_payload_json, payload_fingerprint, fetched_at, source_revision_at,
                    parse_status, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    source_pk, record.source, record.source_record_id, raw_identity,
                    raw_json, payload_fingerprint, record.fetched_at,
                    record.source_revision_at, now,
                ),
            )

        try:
            self._validate_record(record)
        except (TypeError, ValueError) as exc:
            connection.execute(
                "UPDATE source_records SET parse_status='rejected', error_summary=? WHERE source_record_pk=?",
                (str(exc)[:500], source_pk),
            )
            return "rejected"

        home = self._resolve_team(connection, record.home_team_raw, record.source, record.team_type)
        away = self._resolve_team(connection, record.away_team_raw, record.source, record.team_type)
        if home is None or away is None:
            if home is None:
                self._record_unmatched(connection, record.source, record.home_team_raw, record.team_type, now)
            if away is None:
                self._record_unmatched(connection, record.source, record.away_team_raw, record.team_type, now)
            connection.execute(
                "UPDATE source_records SET parse_status='unmatched', error_summary='team_alias_unmatched' WHERE source_record_pk=?",
                (source_pk,),
            )
            return "unmatched"
        if home["team_id"] == away["team_id"]:
            connection.execute(
                "UPDATE source_records SET parse_status='rejected', error_summary='same_team' WHERE source_record_pk=?",
                (source_pk,),
            )
            return "rejected"

        for raw_alias in (record.home_team_raw, record.away_team_raw):
            connection.execute(
                """
                UPDATE unmatched_team_aliases SET status='resolved', last_seen_at=?
                WHERE source=? AND normalized_alias=? AND team_type=?
                """,
                (now, record.source, normalize_alias(raw_alias), record.team_type),
            )

        competition_id = self._ensure_competition(connection, record.competition, now)
        identity = fingerprint({
            "competition_id": competition_id,
            "season": record.season,
            "home_team_id": home["team_id"],
            "away_team_id": away["team_id"],
            "event_date": record.event_date,
        })
        content = fingerprint({
            "identity": identity,
            "kickoff_utc": record.kickoff_utc,
            "status": record.status,
            "home_goals": record.home_goals,
            "away_goals": record.away_goals,
            "neutral": record.neutral,
        })

        match_id = self._find_revision_match(connection, record)
        if match_id is None:
            exact = connection.execute(
                "SELECT match_id FROM matches WHERE identity_fingerprint=? ORDER BY created_at LIMIT 1",
                (identity,),
            ).fetchone()
            match_id = exact["match_id"] if exact else None
        if match_id is None:
            identity_key = f"{record.source}:{record.source_record_id}" if record.source_record_id else identity
            match_id = stable_id("match", identity_key)

        existing_match = connection.execute(
            "SELECT content_fingerprint FROM matches WHERE match_id=?",
            (match_id,),
        ).fetchone()
        values = (
            competition_id, record.season, record.stage, record.event_date,
            record.kickoff_utc, record.source_timezone, record.original_time,
            record.time_precision, home["team_id"], away["team_id"], int(record.neutral),
            record.status, record.home_goals, record.away_goals, identity, content, now, match_id,
        )
        if existing_match is None:
            connection.execute(
                """
                INSERT INTO matches(
                    competition_id, season, stage, event_date, kickoff_utc, source_timezone,
                    original_time, time_precision, home_team_id, away_team_id, neutral,
                    status, home_goals, away_goals, identity_fingerprint, content_fingerprint,
                    created_at, updated_at, match_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values[:-1] + (now, match_id),
            )
            outcome = "inserted"
            self._record_duplicate_candidates(
                connection, match_id, competition_id, record.season,
                home["team_id"], away["team_id"], now,
            )
        elif existing_match["content_fingerprint"] != content:
            connection.execute(
                """
                UPDATE matches SET
                    competition_id=?, season=?, stage=?, event_date=?, kickoff_utc=?,
                    source_timezone=?, original_time=?, time_precision=?, home_team_id=?,
                    away_team_id=?, neutral=?, status=?, home_goals=?, away_goals=?,
                    identity_fingerprint=?, content_fingerprint=?, updated_at=?
                WHERE match_id=?
                """,
                values,
            )
            outcome = "updated"
        else:
            outcome = "skipped"

        connection.execute(
            "INSERT OR IGNORE INTO match_sources(match_id, source_record_pk, linked_at) VALUES (?, ?, ?)",
            (match_id, source_pk, now),
        )
        connection.execute(
            "UPDATE source_records SET parse_status='imported', error_summary=NULL WHERE source_record_pk=?",
            (source_pk,),
        )
        return outcome

    @staticmethod
    def _validate_record(record: SourceRecord):
        if not record.source.strip():
            raise ValueError("source is required")
        if not record.home_team_raw or not record.away_team_raw:
            raise ValueError("home and away teams are required")
        date.fromisoformat(record.event_date)
        if record.time_precision not in {"date", "minute"}:
            raise ValueError("time_precision must be date or minute")
        if record.time_precision == "minute" and not record.kickoff_utc:
            raise ValueError("minute precision requires kickoff_utc")
        if record.status not in {"scheduled", "finished", "postponed", "cancelled"}:
            raise ValueError("invalid match status")
        if record.status == "finished" and (
            record.home_goals is None or record.away_goals is None
        ):
            raise ValueError("finished match requires scores")
        if record.home_goals is not None and (
            isinstance(record.home_goals, bool) or not isinstance(record.home_goals, int)
        ):
            raise ValueError("home_goals must be an integer")
        if record.away_goals is not None and (
            isinstance(record.away_goals, bool) or not isinstance(record.away_goals, int)
        ):
            raise ValueError("away_goals must be an integer")
        if record.home_goals is not None and record.home_goals < 0:
            raise ValueError("home_goals must not be negative")
        if record.away_goals is not None and record.away_goals < 0:
            raise ValueError("away_goals must not be negative")

    def _resolve_team(self, connection, raw_name, source, team_type):
        normalized = normalize_alias(raw_name)
        if team_type in {"national", "club"}:
            exact = connection.execute(
                """
                SELECT t.* FROM team_aliases a JOIN teams t ON t.team_id=a.team_id
                WHERE a.normalized_alias=? AND a.team_type=? AND a.source=?
                """,
                (normalized, team_type, source),
            ).fetchall()
            rows = exact or connection.execute(
                """
                SELECT t.* FROM team_aliases a JOIN teams t ON t.team_id=a.team_id
                WHERE a.normalized_alias=? AND a.team_type=? AND a.source='*'
                """,
                (normalized, team_type),
            ).fetchall()
        else:
            exact = connection.execute(
                """
                SELECT DISTINCT t.* FROM team_aliases a JOIN teams t ON t.team_id=a.team_id
                WHERE a.normalized_alias=? AND a.source=?
                """,
                (normalized, source),
            ).fetchall()
            rows = exact or connection.execute(
                """
                SELECT DISTINCT t.* FROM team_aliases a JOIN teams t ON t.team_id=a.team_id
                WHERE a.normalized_alias=? AND a.source='*'
                """,
                (normalized,),
            ).fetchall()
        unique = {row["team_id"]: row for row in rows}
        return next(iter(unique.values())) if len(unique) == 1 else None

    def resolve_team(self, raw_name: str, source: str, team_type: str):
        connection = self._connection()
        try:
            row = self._resolve_team(connection, raw_name, source, team_type)
            return dict(row) if row else None
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def resolve_team_unique(self, raw_name: str, team_type: str | None = None):
        """Resolve an alias across all sources only when it maps to one team."""
        connection = self._connection()
        normalized = normalize_alias(raw_name)
        clauses = ["a.normalized_alias=?"]
        parameters: list[Any] = [normalized]
        if team_type:
            clauses.append("a.team_type=?")
            parameters.append(team_type)
        try:
            rows = connection.execute(
                f"""
                SELECT DISTINCT t.*
                FROM team_aliases a JOIN teams t ON t.team_id=a.team_id
                WHERE {' AND '.join(clauses)}
                """,
                parameters,
            ).fetchall()
            unique = {row["team_id"]: dict(row) for row in rows}
            return next(iter(unique.values())) if len(unique) == 1 else None
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def get_team(self, team_id: str):
        connection = self._connection()
        try:
            row = connection.execute(
                "SELECT * FROM teams WHERE team_id=?", (team_id,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def list_teams(self, team_type: str | None = None):
        connection = self._connection()
        try:
            if team_type:
                rows = connection.execute(
                    "SELECT * FROM teams WHERE team_type=? ORDER BY canonical_name",
                    (team_type,),
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT * FROM teams ORDER BY team_type, canonical_name"
                ).fetchall()
            return [dict(row) for row in rows]
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def get_competition(self, competition_id: str):
        connection = self._connection()
        try:
            row = connection.execute(
                "SELECT * FROM competitions WHERE competition_id=?", (competition_id,)
            ).fetchone()
            if row:
                return dict(row)
        finally:
            if self.database_path != ":memory:":
                connection.close()
        canonical_name = LEAGUE_NAMES_CN.get(competition_id)
        if canonical_name:
            return {
                "competition_id": competition_id,
                "canonical_name": canonical_name,
                "competition_type": _competition_type(canonical_name),
            }
        return None

    def resolve_competition(self, value: str):
        raw = str(value or "").strip()
        competition_id = LEAGUES.get(raw, raw)
        return self.get_competition(competition_id)

    @staticmethod
    def _record_unmatched(connection, source, raw_alias, team_type, now):
        connection.execute(
            """
            INSERT INTO unmatched_team_aliases(
                source, raw_alias, normalized_alias, team_type,
                first_seen_at, last_seen_at, occurrence_count, status
            ) VALUES (?, ?, ?, ?, ?, ?, 1, 'pending')
            ON CONFLICT(source, normalized_alias, team_type) DO UPDATE SET
                raw_alias=excluded.raw_alias,
                last_seen_at=excluded.last_seen_at,
                occurrence_count=unmatched_team_aliases.occurrence_count + 1
            """,
            (source, raw_alias, normalize_alias(raw_alias), team_type, now, now),
        )

    @staticmethod
    def _ensure_competition(connection, name, now):
        name = str(name or "unknown")
        competition_id = _competition_id(name)
        connection.execute(
            """
            INSERT INTO competitions(
                competition_id, canonical_name, competition_type, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(competition_id) DO UPDATE SET
                canonical_name=excluded.canonical_name,
                updated_at=excluded.updated_at
            """,
            (competition_id, name, _competition_type(name), now, now),
        )
        return competition_id

    @staticmethod
    def _find_revision_match(connection, record):
        if not record.source_record_id:
            return None
        row = connection.execute(
            """
            SELECT ms.match_id
            FROM source_records sr JOIN match_sources ms ON ms.source_record_pk=sr.source_record_pk
            WHERE sr.source=? AND sr.source_record_id=?
            ORDER BY sr.created_at DESC LIMIT 1
            """,
            (record.source, record.source_record_id),
        ).fetchone()
        return row["match_id"] if row else None

    @staticmethod
    def _record_duplicate_candidates(
        connection, match_id, competition_id, season, home_team_id, away_team_id, now
    ):
        rows = connection.execute(
            """
            SELECT match_id FROM matches
            WHERE competition_id=? AND season IS ? AND home_team_id=? AND away_team_id=?
              AND match_id<>?
            """,
            (competition_id, season, home_team_id, away_team_id, match_id),
        ).fetchall()
        for row in rows:
            first, second = sorted((match_id, row["match_id"]))
            candidate_id = stable_id("duplicate", f"{first}:{second}:possible_reschedule")
            connection.execute(
                """
                INSERT OR IGNORE INTO duplicate_candidates(
                    candidate_id, match_id_a, match_id_b, reason, created_at
                ) VALUES (?, ?, ?, 'possible_reschedule', ?)
                """,
                (candidate_id, first, second, now),
            )

    def list_matches(self, filters: Mapping[str, Any] | None = None, as_of: str | None = None):
        connection = self._connection()
        filters = filters or {}
        clauses = []
        parameters: list[Any] = []
        if filters.get("status"):
            clauses.append("m.status=?")
            parameters.append(filters["status"])
        if filters.get("competition_id"):
            clauses.append("m.competition_id=?")
            parameters.append(filters["competition_id"])
        if filters.get("team_id"):
            clauses.append("(m.home_team_id=? OR m.away_team_id=?)")
            parameters.extend([filters["team_id"], filters["team_id"]])
        if filters.get("team_type"):
            clauses.append("ht.team_type=? AND at.team_type=?")
            parameters.extend([filters["team_type"], filters["team_type"]])
        if as_of:
            cutoff_timestamp = normalize_timestamp(as_of)
            cutoff_date = cutoff_timestamp[:10]
            clauses.append(
                "((m.kickoff_utc IS NOT NULL AND m.kickoff_utc < ?) "
                "OR (m.kickoff_utc IS NULL AND m.event_date < ?))"
            )
            parameters.extend([cutoff_timestamp, cutoff_date])
        where = "WHERE " + " AND ".join(clauses) if clauses else ""
        try:
            rows = connection.execute(
                f"""
                SELECT m.*, ht.canonical_name AS home_team, at.canonical_name AS away_team,
                       ht.team_type AS home_team_type, at.team_type AS away_team_type,
                       c.canonical_name AS league
                FROM matches m
                JOIN teams ht ON ht.team_id=m.home_team_id
                JOIN teams at ON at.team_id=m.away_team_id
                JOIN competitions c ON c.competition_id=m.competition_id
                {where}
                ORDER BY m.event_date, COALESCE(m.kickoff_utc, ''), m.match_id
                """,
                parameters,
            ).fetchall()
            return [self._compat_match(row) for row in rows]
        finally:
            if self.database_path != ":memory:":
                connection.close()

    @staticmethod
    def _compat_match(row):
        result = dict(row)
        for internal_field in (
            "identity_fingerprint", "content_fingerprint", "created_at", "updated_at"
        ):
            result.pop(internal_field, None)
        result["date"] = result["event_date"]
        if result.get("kickoff_utc"):
            result["date_time"] = result["kickoff_utc"]
        result["neutral"] = bool(result["neutral"])
        return result

    def get_training_matches(self, before: str, competition_id: str | None = None):
        filters = {"status": "finished"}
        if competition_id:
            filters["competition_id"] = competition_id
        return self.list_matches(filters, as_of=before)

    def list_unmatched_aliases(self):
        connection = self._connection()
        try:
            return [
                dict(row)
                for row in connection.execute(
                    "SELECT * FROM unmatched_team_aliases ORDER BY occurrence_count DESC, raw_alias"
                )
            ]
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def reprocess_unmatched(self):
        connection = self._connection()
        try:
            rows = connection.execute(
                "SELECT source, raw_payload_json, fetched_at FROM source_records WHERE parse_status='unmatched'"
            ).fetchall()
        finally:
            if self.database_path != ":memory:":
                connection.close()
        records = [
            adapt_source_payload(row["source"], json.loads(row["raw_payload_json"]), fetched_at=row["fetched_at"])
            for row in rows
        ]
        if not records:
            return {key: 0 for key in ("fetched", "inserted", "updated", "skipped", "rejected", "unmatched")}
        run_id = self.create_sync_run("reprocess_unmatched")
        return self.import_source_records(records, run_id, sync_type="reprocess_unmatched")

    def save_odds_snapshot(self, snapshot: Mapping[str, Any]):
        required = ("match_id", "company", "captured_at", "home_odds", "draw_odds", "away_odds", "source")
        missing = [key for key in required if snapshot.get(key) is None]
        if missing:
            raise ValueError(f"missing odds fields: {', '.join(missing)}")
        captured_at = normalize_timestamp(snapshot["captured_at"])
        odds_id = stable_id("odds", f"{snapshot['match_id']}:{snapshot['company']}:{captured_at}")
        connection = self._connection()
        try:
            with connection:
                connection.execute(
                    """
                    INSERT INTO odds_snapshots(
                        odds_snapshot_id, match_id, company, captured_at,
                        home_odds, draw_odds, away_odds, source, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(match_id, company, captured_at) DO UPDATE SET
                        home_odds=excluded.home_odds,
                        draw_odds=excluded.draw_odds,
                        away_odds=excluded.away_odds,
                        source=excluded.source
                    """,
                    (
                        odds_id, snapshot["match_id"], snapshot["company"], captured_at,
                        float(snapshot["home_odds"]), float(snapshot["draw_odds"]),
                        float(snapshot["away_odds"]), snapshot["source"], utc_now_iso(),
                    ),
                )
            return odds_id
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def get_pre_match_odds(self, match_id: str, before: str):
        connection = self._connection()
        try:
            cutoff = normalize_timestamp(before)
            row = connection.execute(
                """
                SELECT * FROM odds_snapshots
                WHERE match_id=? AND captured_at<=?
                ORDER BY captured_at DESC LIMIT 1
                """,
                (match_id, cutoff),
            ).fetchone()
            return dict(row) if row else None
        finally:
            if self.database_path != ":memory:":
                connection.close()

    def build_data_fingerprint(
        self,
        filters: Mapping[str, Any] | None = None,
        as_of: str | None = None,
    ):
        matches = self.list_matches(filters, as_of=as_of)
        canonical = [
            {
                key: match.get(key)
                for key in (
                    "match_id", "competition_id", "event_date", "kickoff_utc", "time_precision",
                    "home_team_id", "away_team_id", "neutral", "status", "home_goals", "away_goals",
                )
            }
            for match in matches
        ]
        return fingerprint(canonical)

    def get_data_quality_report(self):
        connection = self._connection()
        try:
            source_status = {
                row["parse_status"]: row["count"]
                for row in connection.execute(
                    "SELECT parse_status, count(*) AS count FROM source_records GROUP BY parse_status"
                )
            }
            return {
                "match_count": connection.execute("SELECT count(*) FROM matches").fetchone()[0],
                "team_count": connection.execute("SELECT count(*) FROM teams").fetchone()[0],
                "source_record_count": connection.execute(
                    "SELECT count(*) FROM source_records"
                ).fetchone()[0],
                "source_status": source_status,
                "pending_unmatched_aliases": connection.execute(
                    "SELECT count(*) FROM unmatched_team_aliases WHERE status='pending'"
                ).fetchone()[0],
                "pending_duplicate_candidates": connection.execute(
                    "SELECT count(*) FROM duplicate_candidates WHERE status='pending'"
                ).fetchone()[0],
                "import_coverage": (
                    source_status.get("imported", 0) / sum(source_status.values())
                    if source_status else 0.0
                ),
            }
        finally:
            if self.database_path != ":memory:":
                connection.close()


def get_default_repository():
    return MatchRepository()
