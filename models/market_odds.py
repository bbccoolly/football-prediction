# models/market_odds.py - 市场赔率隐含概率

import math


class MarketOddsModel:
    """从博彩公司赔率反推市场隐含概率（胜平负基准线）"""

    def __init__(self):
        # 存储各公司赔率
        self.odds_data = {}  # {company: {match_id: {home, draw, away}}}

    def add_odds(self, match_id: str, company: str,
                 home_odds: float, draw_odds: float, away_odds: float):
        """添加一组赔率数据"""
        if company not in self.odds_data:
            self.odds_data[company] = {}
        self.odds_data[company][match_id] = {
            "home": home_odds,
            "draw": draw_odds,
            "away": away_odds,
        }

    def odds_to_probabilities(self, odds: dict) -> dict:
        """赔率转隐含概率（去除水头 overround）"""
        # 倒数
        raw = {k: 1.0 / v for k, v in odds.items()}
        # 水头
        overround = sum(raw.values())
        # 归一化
        probs = {k: v / overround for k, v in raw.items()}
        return probs

    def predict(self, match_id: str = None,
                home_odds: float = None, draw_odds: float = None, away_odds: float = None,
                companies: str = "average") -> dict:
        """预测：可传入特赔率，或从存储中提取"""

        # 如果传入了赔率，直接用
        if home_odds and draw_odds and away_odds:
            probs = self.odds_to_probabilities({
                "home": home_odds, "draw": draw_odds, "away": away_odds
            })
            return {
                "model": "market_odds",
                "home_win": round(probs["home"], 4),
                "draw": round(probs["draw"], 4),
                "away_win": round(probs["away"], 4),
                "source": "manual",
            }

        # 从存储中获取多家平均
        if match_id and self.odds_data:
            all_probs = {"home": [], "draw": [], "away": []}
            for company, matches in self.odds_data.items():
                if match_id in matches:
                    probs = self.odds_to_probabilities(matches[match_id])
                    for k in all_probs:
                        all_probs[k].append(probs[k])

            if all_probs["home"]:
                avg = {k: sum(v) / len(v) for k, v in all_probs.items()}
                return {
                    "model": "market_odds",
                    "home_win": round(avg["home"], 4),
                    "draw": round(avg["draw"], 4),
                    "away_win": round(avg["away"], 4),
                    "source": f"{len(all_probs['home'])} companies avg",
                }

        # 无数据，返回先验
        return {
            "model": "market_odds",
            "home_win": 0.45,
            "draw": 0.28,
            "away_win": 0.27,
            "source": "prior (no data)",
        }

    @staticmethod
    def probability_to_odds(prob: float) -> float:
        """概率转公平赔率"""
        return round(1.0 / prob, 2) if prob > 0 else 999.0
