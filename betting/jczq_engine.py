# betting/jczq_engine.py — 竞彩足球分析引擎核心
"""算法核心：Poisson攻防强度 + Elo + FIFA排名 三模型集成 + 冷门因子 + 让球盘 + 总进球 + EV计算"""

import math
from betting.jczq_team_db import get_team, get_avg_goals, AVG_WC_GOALS, is_national_team


def poisson_pmf(lam: float, k: int) -> float:
    """Poisson 概率质量函数"""
    if lam <= 0:
        lam = 0.001
    if k < 0:
        return 0.0
    # log 计算避免数值溢出
    log_p = -lam + k * math.log(lam)
    for i in range(2, k + 1):
        log_p -= math.log(i)
    return math.exp(log_p)


class JczqEngine:
    """竞彩足球分析引擎"""

    def __init__(self, upset_factor: float = 0.12, is_national: bool = True):
        """
        upset_factor: 冷门因子强度 (0.08~0.15)
        is_national: True=国家队赛事, False=俱乐部赛事
        """
        self.upset_factor = upset_factor
        self.is_national = is_national
        # 集成权重 [Poisson, Elo, FIFA]
        self.ensemble_weights = [0.55, 0.15, 0.30]

    def _calc_lambda(self, home: dict, away: dict, avg_goals: float = None) -> tuple:
        """计算主客队预期进球 lambda (Poisson攻防强度法)"""
        if avg_goals is None:
            avg_goals = AVG_WC_GOALS

        h_att = home["gf"] / avg_goals  # 主队进攻强度
        h_def = home["ga"] / avg_goals  # 主队防守强度
        a_att = away["gf"] / avg_goals
        a_def = away["ga"] / avg_goals

        lam_home = h_att * a_def * avg_goals
        lam_away = a_att * h_def * avg_goals

        # 调整因子
        wc_pressure = 0.94
        form_factor_home = home["form"] / 0.65
        form_factor_away = away["form"] / 0.65

        lam_home = lam_home * wc_pressure * form_factor_home
        lam_away = lam_away * wc_pressure * form_factor_away

        return max(0.1, lam_home), max(0.1, lam_away)

    def _poisson_spf(self, lam_home: float, lam_away: float, max_g: int = 7) -> tuple:
        """Poisson联合分布 → 胜/平/负概率"""
        w_home, draw, w_away = 0.0, 0.0, 0.0
        for g1 in range(max_g + 1):
            for g2 in range(max_g + 1):
                p = poisson_pmf(lam_home, g1) * poisson_pmf(lam_away, g2)
                if g1 > g2:
                    w_home += p
                elif g1 == g2:
                    draw += p
                else:
                    w_away += p
        return w_home, draw, w_away

    def _elo_prob(self, home_elo: float, away_elo: float) -> tuple:
        """Elo模型 → 胜/平/负概率"""
        diff = home_elo - away_elo
        expected_home = 1.0 / (1.0 + math.pow(10, -diff / 400))
        draw_base = 0.26  # 平局基准概率
        w_home = expected_home * (1.0 - draw_base)
        w_away = (1.0 - expected_home) * (1.0 - draw_base)
        return w_home, draw_base, w_away

    def _fifa_prob(self, home_rank: int, away_rank: int) -> tuple:
        """FIFA排名模型 → 胜/平/负概率"""
        diff = away_rank - home_rank  # 正=主队排名高
        goal_diff = -diff * 0.013
        sigma = 1.2
        w_home = 1.0 / (1.0 + math.exp(goal_diff / sigma * 2.5))
        w_away = 1.0 / (1.0 + math.exp(-goal_diff / sigma * 2.5))
        # 剩余为平局
        w_home = max(0.05, min(0.90, w_home))
        w_away = max(0.05, min(0.90, w_away))
        draw = max(0.05, 1.0 - w_home - w_away)
        total = w_home + draw + w_away
        return w_home / total, draw / total, w_away / total

    def _ensemble(self, poisson_probs: tuple, elo_probs: tuple, fifa_probs: tuple) -> tuple:
        """三模型加权集成"""
        w = self.ensemble_weights
        ens_h = w[0] * poisson_probs[0] + w[1] * elo_probs[0] + w[2] * fifa_probs[0]
        ens_d = w[0] * poisson_probs[1] + w[1] * elo_probs[1] + w[2] * fifa_probs[1]
        ens_a = w[0] * poisson_probs[2] + w[1] * elo_probs[2] + w[2] * fifa_probs[2]
        total = ens_h + ens_d + ens_a
        if total > 0:
            return ens_h / total, ens_d / total, ens_a / total
        return 0.40, 0.28, 0.32  # fallback

    def _apply_upset(self, ens_h: float, ens_d: float, ens_a: float) -> tuple:
        """冷门因子调整"""
        fav_is_home = ens_h > ens_a
        shift = self.upset_factor * (0.5 + abs(ens_h - ens_a))

        if fav_is_home:
            u_h = ens_h - shift
            u_d = ens_d + shift * 0.45
            u_a = ens_a + shift * 0.55
        else:
            u_h = ens_h + shift * 0.55
            u_d = ens_d + shift * 0.45
            u_a = ens_a - shift

        # 确保非负
        u_h = max(0.01, u_h)
        u_d = max(0.01, u_d)
        u_a = max(0.01, u_a)

        total = u_h + u_d + u_a
        return u_h / total, u_d / total, u_a / total

    def _calc_rqspf(self, lam_home: float, lam_away: float, fav_is_home: bool, max_g: int = 7) -> tuple:
        """让球胜平负概率 (强队-1盘) → (fav赢2+, fav赢1, 弱队不败)"""
        fav_by2, fav_by1, dog_covers = 0.0, 0.0, 0.0
        for g1 in range(max_g + 1):
            for g2 in range(max_g + 1):
                p = poisson_pmf(lam_home, g1) * poisson_pmf(lam_away, g2)
                if fav_is_home:
                    diff = g1 - g2
                else:
                    diff = g2 - g1
                if diff >= 2:
                    fav_by2 += p
                elif diff == 1:
                    fav_by1 += p
                else:
                    dog_covers += p

        total = fav_by2 + fav_by1 + dog_covers
        if total == 0:
            return 0.20, 0.25, 0.55

        # 冷门因子调整让球盘
        shift = self.upset_factor * 0.5
        rq_f2 = max(0.01, fav_by2 / total - shift * 0.5)
        rq_f1 = max(0.01, fav_by1 / total + shift * 0.1)
        rq_dog = max(0.01, dog_covers / total + shift * 0.4)

        rq_total = rq_f2 + rq_f1 + rq_dog
        return rq_f2 / rq_total, rq_f1 / rq_total, rq_dog / rq_total

    def _calc_total_goals(self, lam_home: float, lam_away: float, max_g: int = 7) -> dict:
        """总进球数概率分布 (0球~7+球)"""
        goals_prob = {}
        for k in range(max_g + 1):
            p = 0.0
            for g1 in range(k + 1):
                g2 = k - g1
                if g2 <= max_g:
                    p += poisson_pmf(lam_home, g1) * poisson_pmf(lam_away, g2)
            goals_prob[k] = p
        # 7+ = 剩余概率
        sum_p = sum(goals_prob.values())
        goals_prob[7] = max(0.0, 1.0 - sum_p)
        return goals_prob

    def analyze(self, home_name: str, away_name: str, league: str = None) -> dict:
        """完整分析一场比赛"""
        home = get_team(home_name)
        away = get_team(away_name)
        # 自动检测赛事类型
        nat = is_national_team(home_name) or is_national_team(away_name)
        avg = AVG_WC_GOALS if nat else get_avg_goals(league)
        lam_home, lam_away = self._calc_lambda(home, away, avg)

        # 1. 计算 lambda

        # 2. Poisson 胜平负
        p_h, p_d, p_a = self._poisson_spf(lam_home, lam_away)

        # 3. Elo 胜平负
        e_h, e_d, e_a = self._elo_prob(home["elo"], away["elo"])

        # 4. FIFA 排名模型
        f_h, f_d, f_a = self._fifa_prob(home["fifa"], away["fifa"])

        # 5. 三模型集成
        ens_h, ens_d, ens_a = self._ensemble((p_h, p_d, p_a), (e_h, e_d, e_a), (f_h, f_d, f_a))

        # 6. 原始集成(未调整冷门)用于计算让球盘
        raw_ens_h, raw_ens_d, raw_ens_a = ens_h, ens_d, ens_a

        # 7. 冷门因子调整
        u_h, u_d, u_a = self._apply_upset(ens_h, ens_d, ens_a)

        # 8. 让球盘概率
        fav_is_home = raw_ens_h > raw_ens_a
        rq_f2, rq_f1, rq_dog = self._calc_rqspf(lam_home, lam_away, fav_is_home)

        # 9. 总进球分布
        goals_dist = self._calc_total_goals(lam_home, lam_away)

        return {
            "lam_home": round(lam_home, 4),
            "lam_away": round(lam_away, 4),
            "total_goals_exp": round(lam_home + lam_away, 2),
            "poisson": {"H": round(p_h, 4), "D": round(p_d, 4), "A": round(p_a, 4)},
            "elo": {"H": round(e_h, 4), "D": round(e_d, 4), "A": round(e_a, 4)},
            "fifa": {"H": round(f_h, 4), "D": round(f_d, 4), "A": round(f_a, 4)},
            "ensemble_raw": {"H": round(raw_ens_h, 4), "D": round(raw_ens_d, 4), "A": round(raw_ens_a, 4)},
            # 最终胜平负概率 (冷门调整后)
            "spf": {"H": round(u_h, 4), "D": round(u_d, 4), "A": round(u_a, 4)},
            # 让球盘概率 (强队-1盘视角)
            "rqspf": {
                "fav2": round(rq_f2, 4),   # 强队赢2+
                "fav1": round(rq_f1, 4),    # 强队赢1
                "dog": round(rq_dog, 4),     # 弱队不败
                "fav_is_home": fav_is_home,
            },
            # 总进球概率分布
            "goals": {k: round(v, 4) for k, v in goals_dist.items()},
        }

    def calc_ev(self, algo_prob: float, odds: float) -> float:
        """计算期望值 EV = algo_prob * odds - 1"""
        if odds <= 0:
            return -1.0
        return algo_prob * odds - 1.0

    @staticmethod
    def market_implied_probs(odds: list) -> list:
        """从赔率推算市场隐含概率（去除水分）"""
        if not odds or all(o == 0 for o in odds):
            return [0.33, 0.34, 0.33]
        imp = [1.0 / o if o > 0 else 0 for o in odds]
        overround = sum(imp)
        if overround == 0:
            return [1.0 / len(odds)] * len(odds)
        return [p / overround for p in imp]

