# models/elo.py - ELO 动态评级系统

import hashlib
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from config import ELO_INITIAL, ELO_K, ELO_HOME_BONUS, ELO_SCALE, PROCESSED_DIR


class EloRating:
    """ELO 动态评级：根据比赛结果更新球队分值，预测胜平负概率"""

    SCHEMA_VERSION = 2

    def __init__(self, storage_path=None):
        self.ratings = {}          # {team_name: elo_score}
        self.history = []          # [{team, elo_before, elo_after, match_id}]
        self.elos_file = str(storage_path or os.path.join(PROCESSED_DIR, "elo_ratings.json"))
        self.data_fingerprint = None
        self.parameter_fingerprint = self._parameter_fingerprint()
        self.match_count = 0
        self.built_at = None

    @staticmethod
    def _parameter_fingerprint():
        params = {
            "initial": ELO_INITIAL,
            "k": ELO_K,
            "home_bonus": ELO_HOME_BONUS,
            "scale": ELO_SCALE,
        }
        payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _normalize_datetime(value):
        if not value:
            return ""
        raw = str(value).strip()
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except ValueError:
            return f"raw:{raw}"

    @classmethod
    def _canonical_matches(cls, matches):
        canonical = []
        for match in matches:
            canonical.append({
                "date_time": cls._normalize_datetime(
                    match.get("date_time") or match.get("date")
                ),
                "match_id": str(match.get("match_id") or ""),
                "league": str(match.get("league") or ""),
                "home_team": str(match.get("home_team") or ""),
                "away_team": str(match.get("away_team") or ""),
                "home_goals": int(match.get("home_goals", 0)),
                "away_goals": int(match.get("away_goals", 0)),
                "neutral": bool(match.get("neutral", False)),
                "importance": float(match.get("importance", 1.0)),
            })
        canonical.sort(key=lambda item: (
            item["date_time"], item["match_id"], item["league"],
            item["home_team"], item["away_team"],
            item["home_goals"], item["away_goals"],
            item["neutral"], item["importance"],
        ))
        return canonical

    @classmethod
    def fingerprint_matches(cls, matches):
        payload = json.dumps(
            cls._canonical_matches(matches),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def reset(self):
        self.ratings = {}
        self.history = []
        self.data_fingerprint = None
        self.parameter_fingerprint = self._parameter_fingerprint()
        self.match_count = 0
        self.built_at = None

    def rebuild(self, matches):
        canonical = self._canonical_matches(matches)
        self.reset()
        for match in canonical:
            self.update(
                match["home_team"], match["away_team"],
                match["home_goals"], match["away_goals"],
                match["neutral"], match["importance"],
                match_id=match["match_id"],
            )
        self.data_fingerprint = self.fingerprint_matches(matches)
        self.match_count = len(canonical)
        self.built_at = datetime.now(timezone.utc).isoformat()
        return self

    def get_rating(self, team: str) -> float:
        return self.ratings.get(team, ELO_INITIAL)

    def expected_score(self, elo_a: float, elo_b: float) -> float:
        """球队 A 对 B 的预期胜率 (0-1)"""
        return 1.0 / (1.0 + math.pow(10, (elo_b - elo_a) / ELO_SCALE))

    def predict_match(self, home_team: str, away_team: str, neutral: bool = False) -> dict:
        """预测一场比赛的胜平负概率"""
        elo_home = self.get_rating(home_team) + (0 if neutral else ELO_HOME_BONUS)
        elo_away = self.get_rating(away_team)

        exp_home = self.expected_score(elo_home, elo_away)
        exp_away = 1.0 - exp_home

        # 使用经验公式将预期胜率拆分为胜平负
        draw_factor = 0.22  # 约 22% 平局率
        prob_draw = draw_factor * (1.0 - abs(exp_home - 0.5) * 1.6)
        prob_draw = max(0.10, min(0.35, prob_draw))

        prob_home_win = (exp_home - prob_draw / 2) * (1.0 - prob_draw) / (1.0 - prob_draw/2)
        prob_away_win = 1.0 - prob_home_win - prob_draw

        prob_home_win = max(0.01, min(0.95, prob_home_win))
        prob_away_win = max(0.01, min(0.95, prob_away_win))
        prob_draw = max(0.05, 1.0 - prob_home_win - prob_away_win)

        return {
            "home_win": round(prob_home_win, 4),
            "draw": round(prob_draw, 4),
            "away_win": round(prob_away_win, 4),
            "elo_home": elo_home,
            "elo_away": elo_away,
        }

    def update(self, home_team: str, away_team: str,
               home_goals: int, away_goals: int,
               neutral: bool = False, importance: float = 1.0,
               match_id: str = ""):
        """赛后更新 ELO 分"""
        elo_home = self.get_rating(home_team) + (0 if neutral else ELO_HOME_BONUS)
        elo_away = self.get_rating(away_team)

        # 实际结果
        if home_goals > away_goals:
            actual_home, actual_away = 1.0, 0.0
        elif home_goals == away_goals:
            actual_home, actual_away = 0.5, 0.5
        else:
            actual_home, actual_away = 0.0, 1.0

        # 净胜球加成
        goal_diff = abs(home_goals - away_goals)
        goal_factor = 1.0 + math.log(max(goal_diff, 1)) * 0.5 if goal_diff > 1 else 1.0

        exp_home = self.expected_score(elo_home, elo_away)
        exp_away = 1.0 - exp_home

        k = ELO_K * importance * goal_factor

        new_home = elo_home + k * (actual_home - exp_home)
        new_away = elo_away + k * (actual_away - exp_away)

        self.ratings[home_team] = new_home - (0 if neutral else ELO_HOME_BONUS)
        self.ratings[away_team] = new_away

        self.history.append({
            "match_id": match_id,
            "home_team": home_team,
            "away_team": away_team,
            "elo_home_before": elo_home,
            "elo_away_before": elo_away,
            "elo_home_after": new_home,
            "elo_away_after": new_away,
        })

    def batch_update(self, matches: list):
        """批量更新：matches = [{home_team, away_team, home_goals, away_goals, neutral, importance}]"""
        for m in matches:
            self.update(
                m["home_team"], m["away_team"],
                m["home_goals"], m["away_goals"],
                m.get("neutral", False),
                m.get("importance", 1.0),
            )

    def save(self):
        path = Path(self.elos_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_name(f".{path.name}.{os.getpid()}.tmp")
        data = {
            "schema_version": self.SCHEMA_VERSION,
            "ratings": self.ratings,
            "history": self.history[-500:],
            "data_fingerprint": self.data_fingerprint,
            "parameter_fingerprint": self._parameter_fingerprint(),
            "match_count": self.match_count,
            "built_at": self.built_at,
        }
        try:
            with temp_path.open("w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    def load(self, expected_fingerprint=None):
        self.reset()
        path = Path(self.elos_file)
        if not path.exists():
            return False
        try:
            with path.open("r", encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError, TypeError):
            return False

        current_parameter_fingerprint = self._parameter_fingerprint()
        if data.get("schema_version") != self.SCHEMA_VERSION:
            return False
        if data.get("parameter_fingerprint") != current_parameter_fingerprint:
            return False
        if expected_fingerprint is not None and data.get("data_fingerprint") != expected_fingerprint:
            return False
        ratings = data.get("ratings")
        history = data.get("history")
        if not isinstance(ratings, dict) or not isinstance(history, list):
            return False

        self.ratings = ratings
        self.history = history
        self.data_fingerprint = data.get("data_fingerprint")
        self.parameter_fingerprint = current_parameter_fingerprint
        self.match_count = int(data.get("match_count", len(history)))
        self.built_at = data.get("built_at")
        return True

    def get_league_rankings(self) -> list:
        """返回 ELO 排名"""
        return sorted(self.ratings.items(), key=lambda x: x[1], reverse=True)
