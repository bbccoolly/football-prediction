"""Explicit, auditable imports for football-data.co.uk historical CSV files."""

from __future__ import annotations

import csv
import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import requests

from data.match_repository import canonical_json, fingerprint, normalize_alias
from data.source_adapters import SourceRecord, adapt_legacy_match


SOURCE = "football-data.co.uk"
SOURCE_HOST = "www.football-data.co.uk"
SEASONS = {
    "1920": "2019/20", "2021": "2020/21", "2122": "2021/22",
    "2223": "2022/23", "2324": "2023/24", "2425": "2024/25",
}
DIVISIONS = {
    "E0": "英超", "SP1": "西甲", "D1": "德甲", "I1": "意甲", "F1": "法甲",
}
ODDS_COLUMNS = {
    "B365": ("B365CH", "B365CD", "B365CA"),
    "Pinnacle": ("PSCH", "PSCD", "PSCA"),
    "William Hill": ("WHCH", "WHCD", "WHCA"),
    "Betway": ("BWCH", "BWCD", "BWCA"),
    "Interwetten": ("IWCH", "IWCD", "IWCA"),
    "BetVictor": ("VCCH", "VCCD", "VCCA"),
    "1xBet": ("1XBCH", "1XBCD", "1XBCA"),
    "Betfair": ("BFCH", "BFCD", "BFCA"),
}
REQUIRED_COLUMNS = {"Div", "Date", "HomeTeam", "AwayTeam", "FTHG", "FTAG", "FTR"}


class FootballDataError(ValueError):
    pass


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class HistoricalClosingOddsInput:
    source_record_id: str
    company: str
    home_odds: float
    draw_odds: float
    away_odds: float
    observed_at: str
    source_file_sha256: str


@dataclass(frozen=True)
class FootballDataRecord:
    record: SourceRecord
    source_file_sha256: str
    closing_odds: tuple[HistoricalClosingOddsInput, ...]


def source_url(season_code: str, division: str) -> str:
    if season_code not in SEASONS or division not in DIVISIONS:
        raise FootballDataError("不支持的赛事或赛季")
    return f"https://{SOURCE_HOST}/mmz4281/{season_code}/{division}.csv"


def _decode_csv(content: bytes) -> str:
    for encoding in ("utf-8-sig", "cp1252"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise FootballDataError("CSV 编码无法解析")


def _valid_odds(values) -> tuple[float, float, float] | None:
    try:
        parsed = tuple(float(str(value).strip()) for value in values)
    except (TypeError, ValueError):
        return None
    if len(parsed) != 3 or any(not math.isfinite(value) or value <= 1 for value in parsed):
        return None
    return parsed


def _date(value: str) -> str:
    try:
        return datetime.strptime(value.strip(), "%d/%m/%Y").date().isoformat()
    except (AttributeError, ValueError) as exc:
        raise FootballDataError(f"日期无效: {value!r}") from exc


def parse_csv(content: bytes, *, season_code: str, division: str, observed_at: str) -> tuple[FootballDataRecord, ...]:
    """Parse one immutable source file without touching the repository."""
    if season_code not in SEASONS or division not in DIVISIONS:
        raise FootballDataError("不支持的赛事或赛季")
    rows = csv.DictReader(_decode_csv(content).splitlines())
    if not rows.fieldnames or not REQUIRED_COLUMNS <= set(rows.fieldnames):
        raise FootballDataError("CSV 缺少必要字段")
    file_hash = sha256_bytes(content)
    parsed = []
    for number, row in enumerate(rows, start=2):
        if not any((value or "").strip() for value in row.values()):
            continue
        try:
            home = (row.get("HomeTeam") or "").strip()
            away = (row.get("AwayTeam") or "").strip()
            if not home or not away:
                raise FootballDataError("球队名称为空")
            home_goals, away_goals = int(row["FTHG"]), int(row["FTAG"])
            expected = "H" if home_goals > away_goals else "A" if away_goals > home_goals else "D"
            if row["FTR"].strip().upper() != expected:
                raise FootballDataError("FTR 与比分不一致")
            event_date = _date(row["Date"])
        except (KeyError, TypeError, ValueError) as exc:
            raise FootballDataError(f"第 {number} 行无效: {exc}") from exc
        source_record_id = fingerprint({
            "division": division, "season": SEASONS[season_code], "date": event_date,
            "home": normalize_alias(home), "away": normalize_alias(away),
        })
        payload = dict(row)
        payload.update({
            "match_id": source_record_id, "league": DIVISIONS[division],
            "season": SEASONS[season_code], "date": event_date,
            "home_team": home, "away_team": away,
            "home_goals": home_goals, "away_goals": away_goals,
            "status": "finished", "team_type": "club", "source_row": number,
            "source_file_sha256": file_hash,
        })
        record = adapt_legacy_match(payload, fetched_at=observed_at, source=SOURCE)
        odds = []
        for company, columns in ODDS_COLUMNS.items():
            values = _valid_odds(tuple(row.get(column) for column in columns))
            if values:
                odds.append(HistoricalClosingOddsInput(
                    source_record_id=source_record_id, company=company,
                    home_odds=values[0], draw_odds=values[1], away_odds=values[2],
                    observed_at=observed_at, source_file_sha256=file_hash,
                ))
        parsed.append(FootballDataRecord(record, file_hash, tuple(odds)))
    if not parsed:
        raise FootballDataError("CSV 没有可导入的完场比赛")
    return tuple(parsed)


def build_manifest(files: Iterable[dict]) -> dict:
    canonical_files = sorted(files, key=lambda item: (item["season_code"], item["division"]))
    basis = {
        "schema_version": 1, "source": SOURCE,
        "files": [{key: item[key] for key in ("season_code", "division", "url", "sha256", "bytes")} for item in canonical_files],
    }
    manifest_fingerprint = fingerprint(basis)
    return {
        **basis, "batch_id": f"fd-{manifest_fingerprint[:16]}",
        "manifest_fingerprint": manifest_fingerprint,
        "fetched_at": utc_now_iso(), "files": canonical_files,
    }


def fetch_batch(output_root: Path, *, session=requests, timeout=20) -> tuple[Path, dict]:
    """Download the fixed source matrix only when called explicitly by the CLI."""
    temporary = output_root / ".football-data-download"
    temporary.mkdir(parents=True, exist_ok=True)
    files = []
    for season_code in SEASONS:
        for division in DIVISIONS:
            url = source_url(season_code, division)
            response = session.get(url, timeout=timeout, allow_redirects=True)
            if response.url.split("/")[2].lower() != SOURCE_HOST or response.status_code != 200:
                raise FootballDataError(f"下载失败: {url}")
            content = response.content
            if not content or content.lstrip().lower().startswith(b"<html"):
                raise FootballDataError(f"来源不是有效 CSV: {url}")
            # Validate before retaining the source artifact.
            parse_csv(content, season_code=season_code, division=division, observed_at=utc_now_iso())
            filename = f"{season_code}-{division}.csv"
            path = temporary / filename
            path.write_bytes(content)
            files.append({
                "season_code": season_code, "division": division, "url": url,
                "sha256": sha256_bytes(content), "bytes": len(content), "filename": filename,
                "etag": response.headers.get("ETag"), "last_modified": response.headers.get("Last-Modified"),
            })
    manifest = build_manifest(files)
    batch_path = output_root / manifest["batch_id"]
    if batch_path.exists():
        return batch_path, json.loads((batch_path / "manifest.json").read_text(encoding="utf-8"))
    batch_path.parent.mkdir(parents=True, exist_ok=True)
    temporary.replace(batch_path)
    (batch_path / "manifest.json").write_text(canonical_json(manifest), encoding="utf-8")
    return batch_path, manifest


def load_batch(path: Path) -> tuple[dict, tuple[FootballDataRecord, ...]]:
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
    expected = build_manifest(manifest["files"])
    if manifest.get("manifest_fingerprint") != expected["manifest_fingerprint"]:
        raise FootballDataError("batch manifest 指纹不匹配")
    records = []
    observed_at = manifest["fetched_at"]
    for entry in manifest["files"]:
        content = (path / entry["filename"]).read_bytes()
        if sha256_bytes(content) != entry["sha256"]:
            raise FootballDataError(f"源文件校验和不匹配: {entry['filename']}")
        records.extend(parse_csv(content, season_code=entry["season_code"], division=entry["division"], observed_at=observed_at))
    return manifest, tuple(records)
