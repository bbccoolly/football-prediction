from datetime import datetime, timezone

from data.fifa_sync import fetch_recent_fifa_source_records


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        return self._payload


def fifa_match():
    return {
        "IdMatch": "fifa-1",
        "Date": "2026-07-20T18:00:00Z",
        "CompetitionName": [{"Description": "FIFA World Cup"}],
        "Home": {"TeamName": [{"Description": "France"}]},
        "Away": {"TeamName": [{"Description": "Spain"}]},
        "HomeTeamScore": 1,
        "AwayTeamScore": 0,
    }


def test_fifa_fetch_returns_records_without_writing_state():
    calls = []

    def request_get(*_args, **_kwargs):
        calls.append(1)
        return FakeResponse({"Results": [fifa_match()] if len(calls) == 1 else []})

    result = fetch_recent_fifa_source_records(
        request_get=request_get,
        now=datetime(2026, 7, 23, tzinfo=timezone.utc),
        days=2,
    )

    assert result["fetched"] == 1
    assert len(result["records"]) == 1
    assert result["records"][0].source_record_id == "fifa-1"
    assert result["errors"] == []


def test_fifa_fetch_distinguishes_request_and_parse_failures():
    responses = iter([
        FakeResponse({}, status_code=503),
        FakeResponse({"Results": {"not": "a list"}}),
    ])

    result = fetch_recent_fifa_source_records(
        request_get=lambda *_args, **_kwargs: next(responses),
        now=datetime(2026, 7, 23, tzinfo=timezone.utc),
        days=3,
    )

    assert [error["status"] for error in result["errors"]] == [
        "request_failed", "parse_failed"
    ]
