# features/player_impact.py - 人员影响量化

class PlayerImpact:
    """量化球员缺阵对球队的影响"""

    def __init__(self):
        # {team: {player_name: importance_score (1-10)}}
        self.squad = {}
        # {team: [injured_player_names]}
        self.injuries = {}

    def set_squad(self, team: str, players: dict):
        """players = {name: importance (1-10)}"""
        self.squad[team] = players

    def set_injuries(self, team: str, missing_players: list):
        """设置缺阵球员名单"""
        self.injuries[team] = missing_players

    def squad_completeness(self, team: str) -> float:
        """计算阵容完整度 0.0 - 1.0"""
        if team not in self.squad or not self.squad[team]:
            return 1.0

        total_importance = sum(self.squad[team].values())
        if total_importance == 0:
            return 1.0

        missing = set(self.injuries.get(team, []))
        lost_importance = 0

        for player, importance in self.squad[team].items():
            if player in missing:
                lost_importance += importance * 1.2  # 缺阵影响放大 1.2 倍

        completeness = 1.0 - (lost_importance / total_importance)
        return max(0.3, completeness)  # 最低 30%，避免极端

    def impact_factor(self, team: str) -> float:
        """影响因子：1.0 为全主力，<1.0 表示削弱"""
        return self.squad_completeness(team)

    def both_teams_impact(self, home_team: str, away_team: str) -> dict:
        """两队人员影响"""
        return {
            "home_completeness": round(self.squad_completeness(home_team), 3),
            "away_completeness": round(self.squad_completeness(away_team), 3),
            "home_missing": self.injuries.get(home_team, []),
            "away_missing": self.injuries.get(away_team, []),
        }
