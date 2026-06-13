# features/builder.py - 特征向量构造器

import numpy as np


class FeatureBuilder:
    """汇总所有模型、人员、场地信息，构造标准化特征向量"""

    FEATURE_NAMES = [
        "elo_diff",           # ELO 分差
        "massey_diff",        # Massey 分差
        "form_diff",          # 状态分差
        "home_attack",        # 主队进攻强度
        "home_defense",       # 主队防守强度
        "away_attack",        # 客队进攻强度
        "away_defense",       # 客队防守强度
        "h2h_home_win_rate",  # 交锋主队胜率
        "h2h_draw_rate",      # 交锋平局率
        "h2h_avg_goals",      # 交锋场均进球
        "home_ppg",           # 主队近期场均积分(归一化)
        "away_ppg",           # 客队近期场均积分(归一化)
        "home_gd_avg",        # 主队近期场均净胜球
        "away_gd_avg",        # 客队近期场均净胜球
        "squad_completeness_home",  # 主队阵容完整度
        "squad_completeness_away",  # 客队阵容完整度
        "home_advantage",     # 主场优势系数
        "neutral",            # 是否中立场
    ]

    def __init__(self):
        pass

    def build(self,
              elo_home: float, elo_away: float,
              atk_home: float, atk_away: float,
              def_home: float, def_away: float,
              form_home: dict, form_away: dict,
              h2h_stats: dict,
              squad_home: float, squad_away: float,
              home_adv: float, neutral: bool) -> dict:
        """构造特征字典 + numpy 向量"""

        features = {
            "elo_diff": elo_home - elo_away,
            "massey_diff": 0.0,  # 后续填充
            "form_diff": form_home.get("form_score", 0.5) - form_away.get("form_score", 0.5),
            "home_attack": atk_home,
            "home_defense": def_home,
            "away_attack": atk_away,
            "away_defense": def_away,
            "h2h_home_win_rate": h2h_stats.get("a_win_rate", 0.33),
            "h2h_draw_rate": h2h_stats.get("draw_rate", 0.34),
            "h2h_avg_goals": h2h_stats.get("avg_goals", 2.7),
            "home_ppg": form_home.get("ppg", 0.5),
            "away_ppg": form_away.get("ppg", 0.5),
            "home_gd_avg": form_home.get("goal_diff_avg", 0.0),
            "away_gd_avg": form_away.get("goal_diff_avg", 0.0),
            "squad_completeness_home": squad_home,
            "squad_completeness_away": squad_away,
            "home_advantage": home_adv if not neutral else 0.0,
            "neutral": 1.0 if neutral else 0.0,
        }

        vec = np.array([features[name] for name in self.FEATURE_NAMES])
        features["vector"] = vec

        return features
