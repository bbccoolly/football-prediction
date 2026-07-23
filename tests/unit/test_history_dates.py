from datetime import datetime, timezone

import pytest

from data.history_db import fetch_500_history_date, recent_completed_dates


def test_recent_completed_dates_cross_year_without_including_today():
    dates = recent_completed_dates(
        now=datetime(2026, 1, 3, 8, 0, tzinfo=timezone.utc),
        days=5,
    )

    assert dates == [
        "2026-01-02",
        "2026-01-01",
        "2025-12-31",
        "2025-12-30",
        "2025-12-29",
    ]


def test_recent_completed_dates_support_leap_day():
    dates = recent_completed_dates(now=datetime(2024, 3, 2), days=3)

    assert dates == ["2024-03-01", "2024-02-29", "2024-02-28"]


@pytest.mark.parametrize("days", [0, -1])
def test_recent_completed_dates_require_positive_days(days):
    with pytest.raises(ValueError):
        recent_completed_dates(now=datetime(2026, 1, 1), days=days)


class FakeResponse:
    def __init__(self, text, status_code=200):
        self.text = text
        self.status_code = status_code
        self.encoding = None


def test_history_fetch_distinguishes_success_and_no_matches():
    success = fetch_500_history_date(
        "2026-07-22",
        request_get=lambda *_args, **_kwargs: FakeResponse(
            "<tr><a>主队甲</a><a>客队乙</a>2 - 1</tr>"
        ),
    )
    empty = fetch_500_history_date(
        "2026-07-21",
        request_get=lambda *_args, **_kwargs: FakeResponse("<html></html>"),
    )

    assert success["status"] == "success"
    assert success["matches"][0]["home_goals"] == 2
    assert empty["status"] == "no_matches"


def test_history_fetch_distinguishes_request_and_parse_failures():
    def fail_request(*_args, **_kwargs):
        raise RuntimeError("offline")

    request_failure = fetch_500_history_date(
        "2026-07-22",
        request_get=fail_request,
    )
    parse_failure = fetch_500_history_date(
        "2026-07-22",
        request_get=lambda *_args, **_kwargs: FakeResponse(
            "<tr><a>只有一队</a>2 - 1</tr>"
        ),
    )

    assert request_failure["status"] == "request_failed"
    assert parse_failure["status"] == "parse_failed"
