"""
data/history_db.py -- 持久化历史比赛数据库
"""

import json
import os
import warnings
from datetime import datetime, timedelta
from pathlib import Path

from data.match_repository import DEFAULT_DATABASE_PATH, MatchRepository
from data.source_adapters import adapt_legacy_match

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed", "match_history.json")
_legacy_warning_emitted = False


def recent_completed_dates(now=None, days=14):
    """返回最近已结束自然日，按新到旧排序且不包含当天。"""
    if days <= 0:
        raise ValueError("days must be positive")
    current = now or datetime.now()
    today = current.date()
    return [
        (today - timedelta(days=offset)).isoformat()
        for offset in range(1, days + 1)
    ]


def fetch_500_history_date(date_str, request_get=None):
    """抓取单日完场数据，并明确区分空数据、请求失败和解析失败。"""
    import re
    import requests

    get = request_get or requests.get
    try:
        response = get(
            f"https://live.500.com/?e={date_str}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
    except Exception as exc:
        return {
            "date": date_str,
            "status": "request_failed",
            "matches": [],
            "error": str(exc),
        }

    if response.status_code != 200:
        return {
            "date": date_str,
            "status": "request_failed",
            "matches": [],
            "error": f"http_{response.status_code}",
        }

    response.encoding = "gb2312"
    skip = [
        "退出", "个人中心", "全选", "反选", "设为首页", "首页", "开奖",
        "登录", "注册", "比分", "完", "直播", "待",
    ]
    matches = []
    scored_rows = 0
    try:
        rows = re.findall(r"<tr[^>]*?>(.*?)</tr>", response.text, re.DOTALL)
        for row in rows:
            score = re.search(r"(\d+)\s*-\s*(\d+)", row)
            if not score:
                continue
            scored_rows += 1
            teams = re.findall(r"<a[^>]*?>([^<]{2,30})</a>", row)
            if len(teams) < 2:
                continue
            home, away = teams[0].strip(), teams[-1].strip()
            if any(word in home or word in away for word in skip):
                continue
            matches.append({
                "home_team": home,
                "away_team": away,
                "home_goals": int(score.group(1)),
                "away_goals": int(score.group(2)),
                "league": "",
                "date": date_str,
            })
    except (AttributeError, TypeError, ValueError) as exc:
        return {
            "date": date_str,
            "status": "parse_failed",
            "matches": [],
            "error": str(exc),
        }

    if matches:
        status = "success"
    elif scored_rows:
        status = "parse_failed"
    else:
        status = "no_matches"
    return {"date": date_str, "status": status, "matches": matches, "error": None}


def fetch_recent_500_history(now=None, days=14, request_get=None):
    results = [
        fetch_500_history_date(date_str, request_get=request_get)
        for date_str in recent_completed_dates(now=now, days=days)
    ]
    return {
        "matches": [match for result in results for match in result["matches"]],
        "days": results,
    }

def _database_path():
    return Path(os.environ.get("FOOTBALL_DB_PATH", DEFAULT_DATABASE_PATH))


def load_history(database_path=None, legacy_path=None):
    """Read SQLite when initialized, otherwise use the legacy JSON without side effects."""
    global _legacy_warning_emitted
    database = Path(database_path) if database_path else _database_path()
    if database.exists():
        return MatchRepository(database).list_matches()

    legacy = Path(legacy_path or DB_FILE)
    if not legacy.exists():
        return []
    if not _legacy_warning_emitted:
        warnings.warn(
            "旧版比赛 JSON 只读回退已弃用，请运行 scripts/migrate_history.py",
            DeprecationWarning,
            stacklevel=2,
        )
        _legacy_warning_emitted = True
    with legacy.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    matches = payload.get("matches", []) if isinstance(payload, dict) else payload
    return list(matches) if isinstance(matches, list) else []


def save_history(matches, database_path=None):
    """Compatibility writer that only imports into an initialized SQLite repository."""
    repository = MatchRepository(database_path or _database_path())
    run_id = repository.create_sync_run("manual_history_import")
    records = [adapt_legacy_match(match, source="manual") for match in matches]
    return repository.import_source_records(records, run_id, sync_type="manual_history_import")


def add_match(match, database_path=None):
    """Compatibility single-record writer; JSON writes are intentionally unsupported."""
    repository = MatchRepository(database_path or _database_path())
    run_id = repository.create_sync_run("manual_match")
    counts = repository.import_source_records(
        [adapt_legacy_match(match, source="manual")],
        run_id,
        sync_type="manual_match",
    )
    return counts["inserted"] > 0 or counts["updated"] > 0

if __name__ == "__main__":
    m = load_history()
    print(f"Loaded {len(m)} matches")
