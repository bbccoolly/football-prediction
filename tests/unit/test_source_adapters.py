from data.source_adapters import (
    adapt_500_history_match,
    adapt_fifa_match,
    adapt_openligadb_match,
)


def test_openligadb_adapter_preserves_source_id_and_minute_precision():
    record = adapt_openligadb_match({
        "matchID": 123,
        "matchDateTimeUTC": "2025-01-01T18:30:00Z",
        "team1": {"teamName": "FC Bayern München"},
        "team2": {"teamName": "Borussia Dortmund"},
        "matchResults": [{"pointsTeam1": 2, "pointsTeam2": 1}],
    })

    assert record.source == "openligadb"
    assert record.source_record_id == "123"
    assert record.time_precision == "minute"
    assert record.kickoff_utc.endswith("+00:00")


def test_500_adapter_keeps_date_precision_and_raw_names():
    record = adapt_500_history_match({
        "home_team": "主队甲",
        "away_team": "客队乙",
        "home_goals": 0,
        "away_goals": 0,
        "league": "测试联赛",
        "date": "2025-01-01",
    })

    assert record.source == "500.com"
    assert record.time_precision == "date"
    assert record.home_team_raw == "主队甲"


def test_fifa_adapter_uses_official_id_and_national_namespace():
    record = adapt_fifa_match({
        "IdMatch": "fifa-1",
        "Date": "2025-01-01T20:00:00Z",
        "CompetitionName": [{"Locale": "en-GB", "Description": "FIFA World Cup"}],
        "Home": {"TeamName": [{"Locale": "en-GB", "Description": "France"}]},
        "Away": {"TeamName": [{"Locale": "en-GB", "Description": "Spain"}]},
        "HomeTeamScore": 1,
        "AwayTeamScore": 0,
        "MatchStatus": 8,
    })

    assert record.source_record_id == "fifa-1"
    assert record.competition == "世界杯"
    assert record.team_type == "national"
