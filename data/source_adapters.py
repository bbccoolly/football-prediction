"""Pure adapters from external payloads to repository source records."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Mapping

from config import CLUB_TEAMS, NATIONAL_TEAMS


NATIONAL_COMPETITIONS = {
    "世界杯", "欧洲杯", "美洲杯", "欧国联", "世预赛", "友谊赛",
    "FIFA World Cup", "World Cup", "UEFA European Championship",
    "Copa America", "Friendlies", "FIFA Friendlies",
}

CLUB_COMPETITIONS = {
    "英超", "西甲", "德甲", "意甲", "法甲", "中超", "欧冠", "欧联杯",
    "亚冠", "日职", "K联赛", "沙特联", "bundesliga",
}


@dataclass(frozen=True)
class SourceRecord:
    source: str
    source_record_id: str | None
    fetched_at: str
    raw_payload: Mapping[str, Any]
    competition: str
    season: str | None
    stage: str | None
    event_date: str
    kickoff_utc: str | None
    source_timezone: str | None
    original_time: str | None
    time_precision: str
    home_team_raw: str
    away_team_raw: str
    team_type: str
    neutral: bool
    status: str
    home_goals: int | None
    away_goals: int | None
    source_revision_at: str | None = None

    def as_dict(self):
        return asdict(self)


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def infer_team_type(home: str, away: str, competition: str) -> str:
    national = set(NATIONAL_TEAMS)
    clubs = set(CLUB_TEAMS)
    if home in national and away in national:
        return "national"
    if home in clubs and away in clubs:
        return "club"
    if competition in NATIONAL_COMPETITIONS or "World Cup" in competition:
        return "national"
    if competition in CLUB_COMPETITIONS:
        return "club"
    return "unknown"


def _normalize_time(value: Any):
    original = str(value or "").strip()
    if not original:
        return "", None, "date"
    event_date = original[:10]
    if len(original) <= 10:
        return event_date, None, "date"
    try:
        parsed = datetime.fromisoformat(original.replace("Z", "+00:00"))
    except ValueError:
        return event_date, None, "date"
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return event_date, parsed.astimezone(timezone.utc).isoformat(), "minute"


def _normalize_score(value: Any):
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def adapt_legacy_match(
    payload: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
    source: str = "legacy_json",
) -> SourceRecord:
    original_time = payload.get("date_time") or payload.get("kickoff_utc") or payload.get("date")
    event_date, kickoff_utc, precision = _normalize_time(original_time)
    competition = str(payload.get("league") or payload.get("competition") or "unknown")
    home = str(payload.get("home_team") or "").strip()
    away = str(payload.get("away_team") or "").strip()
    home_goals = payload.get("home_goals")
    away_goals = payload.get("away_goals")
    status = payload.get("status") or (
        "finished" if home_goals is not None and away_goals is not None else "scheduled"
    )
    return SourceRecord(
        source=source,
        source_record_id=str(payload.get("match_id")) if payload.get("match_id") else None,
        fetched_at=fetched_at or utc_now_iso(),
        raw_payload=dict(payload),
        competition=competition,
        season=str(payload.get("season")) if payload.get("season") is not None else None,
        stage=str(payload.get("stage")) if payload.get("stage") is not None else None,
        event_date=event_date,
        kickoff_utc=kickoff_utc,
        source_timezone=payload.get("source_timezone"),
        original_time=str(original_time or ""),
        time_precision=payload.get("time_precision") or precision,
        home_team_raw=home,
        away_team_raw=away,
        team_type=payload.get("team_type") or infer_team_type(home, away, competition),
        neutral=bool(payload.get("neutral", False)),
        status=str(status),
        home_goals=_normalize_score(home_goals),
        away_goals=_normalize_score(away_goals),
        source_revision_at=payload.get("updated_at"),
    )


def adapt_openligadb_match(
    payload: Mapping[str, Any],
    *,
    fetched_at: str | None = None,
    competition: str = "德甲",
    season: str | None = None,
) -> SourceRecord:
    results = payload.get("matchResults") or []
    result = results[-1] if results else {}
    record = {
        "match_id": payload.get("matchID"),
        "home_team": (payload.get("team1") or {}).get("teamName"),
        "away_team": (payload.get("team2") or {}).get("teamName"),
        "home_goals": result.get("pointsTeam1"),
        "away_goals": result.get("pointsTeam2"),
        "league": competition,
        "season": season,
        "date_time": payload.get("matchDateTimeUTC") or payload.get("matchDateTime"),
        "team_type": "club",
        "status": "finished" if result.get("pointsTeam1") is not None else "scheduled",
    }
    adapted = adapt_legacy_match(record, fetched_at=fetched_at, source="openligadb")
    return SourceRecord(**{**adapted.as_dict(), "raw_payload": dict(payload)})


def _localized_description(value: Any) -> str:
    if isinstance(value, list):
        english = next(
            (item.get("Description", "") for item in value if str(item.get("Locale", "")).startswith("en")),
            "",
        )
        return english or (value[0].get("Description", "") if value else "")
    return str(value or "")


def adapt_fifa_match(payload: Mapping[str, Any], *, fetched_at: str | None = None) -> SourceRecord:
    home = payload.get("Home") or {}
    away = payload.get("Away") or {}
    competition = _localized_description(payload.get("CompetitionName")) or "FIFA"
    record = {
        "match_id": payload.get("IdMatch") or payload.get("MatchId") or payload.get("Id"),
        "home_team": _localized_description(home.get("TeamName")) or home.get("ShortClubName"),
        "away_team": _localized_description(away.get("TeamName")) or away.get("ShortClubName"),
        "home_goals": payload.get("HomeTeamScore"),
        "away_goals": payload.get("AwayTeamScore"),
        "league": "世界杯" if "World Cup" in competition else competition,
        "date_time": payload.get("Date"),
        "team_type": "national",
        "neutral": bool(payload.get("IsNeutralVenue", True)),
        "status": "finished" if payload.get("HomeTeamScore") is not None else "scheduled",
    }
    adapted = adapt_legacy_match(record, fetched_at=fetched_at, source="fifa")
    return SourceRecord(**{**adapted.as_dict(), "raw_payload": dict(payload)})


def adapt_500_history_match(payload: Mapping[str, Any], *, fetched_at: str | None = None) -> SourceRecord:
    return adapt_legacy_match(payload, fetched_at=fetched_at, source="500.com")


def adapt_source_payload(source: str, payload: Mapping[str, Any], *, fetched_at: str | None = None):
    if source == "openligadb":
        return adapt_openligadb_match(payload, fetched_at=fetched_at)
    if source == "fifa":
        return adapt_fifa_match(payload, fetched_at=fetched_at)
    if source == "500.com":
        return adapt_500_history_match(payload, fetched_at=fetched_at)
    return adapt_legacy_match(payload, fetched_at=fetched_at, source=source)
