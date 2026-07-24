"""Explicit FIFA history synchronization helpers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from data.source_adapters import adapt_fifa_match


FIFA_MATCHES_URL = "https://api.fifa.com/api/v3/calendar/matches"
HEADERS = {"User-Agent": "Mozilla/5.0"}


def fetch_recent_fifa_source_records(request_get=None, now=None, days=14):
    """Fetch recent men's World Cup records without writing repository state."""
    get = request_get or requests.get
    current = now or datetime.now(timezone.utc)
    records = []
    fetched = 0
    errors = []

    for days_back in range(days, -1, -3):
        start = current - timedelta(days=min(days_back + 2, days))
        end = current - timedelta(days=max(days_back - 1, 0))
        start_text = start.strftime("%Y-%m-%dT00:00:00Z")
        end_text = end.strftime("%Y-%m-%dT23:59:59Z")
        try:
            response = get(
                FIFA_MATCHES_URL,
                params={
                    "language": "en",
                    "count": 200,
                    "from": start_text,
                    "to": end_text,
                },
                headers=HEADERS,
                timeout=15,
            )
            if response.status_code != 200:
                errors.append({
                    "from": start_text,
                    "to": end_text,
                    "status": "request_failed",
                    "error": f"http_{response.status_code}",
                })
                continue
            payload = response.json()
            results = payload.get("Results", [])
            if not isinstance(results, list):
                raise ValueError("Results must be a list")
            for match in results:
                names = match.get("CompetitionName") or [{}]
                competition = names[0].get("Description", "") if names else ""
                if "World Cup" not in competition or "Women" in competition:
                    continue
                fetched += 1
                try:
                    records.append(adapt_fifa_match(match))
                except (KeyError, TypeError, ValueError) as exc:
                    errors.append({
                        "from": start_text,
                        "to": end_text,
                        "status": "parse_failed",
                        "error": str(exc),
                    })
        except requests.RequestException as exc:
            errors.append({
                "from": start_text,
                "to": end_text,
                "status": "request_failed",
                "error": str(exc),
            })
        except (TypeError, ValueError) as exc:
            errors.append({
                "from": start_text,
                "to": end_text,
                "status": "parse_failed",
                "error": str(exc),
            })

    return {"records": records, "fetched": fetched, "errors": errors}
