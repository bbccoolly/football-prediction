# betting/jczq_planner.py — 投注方案生成器
"""+EV筛选、单关/串关约束、100元资金分配、方案输出"""

import math
from betting.jczq_engine import JczqEngine

SPF_LABELS = ["胜", "平", "负"]
RQSPF_LABELS = ["胜(赢2+)", "平(赢1)", "负(不败)"]
GOALS_LABELS = ["0球", "1球", "2球", "3球", "4球", "5球", "6球", "7+球"]


class JczqPlanner:

    def __init__(self, engine=None, budget=100.0, unit_price=2.0):
        self.engine = engine or JczqEngine()
        self.budget = budget
        self.unit_price = unit_price

    def analyze_matches(self, matches):
        results = []
        for m in matches:
            algo = self.engine.analyze(m["home"], m["away"], m.get("league"))
            odds = m.get("odds", {})
            spf_mkt = self.engine.market_implied_probs(odds.get("spf", [0, 0, 0]))
            spf_ev = []
            for i in range(3):
                ev = self.engine.calc_ev(
                    [algo["spf"]["H"], algo["spf"]["D"], algo["spf"]["A"]][i],
                    odds.get("spf", [0, 0, 0])[i])
                spf_ev.append(round(ev, 4))
            rq_odds = odds.get("rqspf", [0, 0, 0])
            rq_ev = []
            if any(o > 0 for o in rq_odds):
                rq_ev = [
                    round(self.engine.calc_ev(algo["rqspf"]["fav2"], rq_odds[0]), 4),
                    round(self.engine.calc_ev(algo["rqspf"]["fav1"], rq_odds[1]), 4),
                    round(self.engine.calc_ev(algo["rqspf"]["dog"], rq_odds[2]), 4)]
            goals_odds = odds.get("goals", [0] * 8)
            goals_ev = {}
            for k in range(min(8, len(goals_odds))):
                if isinstance(goals_odds[k], (int, float)) and goals_odds[k] > 0:
                    goals_ev[k] = round(self.engine.calc_ev(algo["goals"].get(k, 0), goals_odds[k]), 4)
            results.append({
                "id": m["id"], "league": m.get("league", ""), "time": m.get("time", ""),
                "home": m["home"], "away": m["away"],
                "single_bet": m.get("single_bet", False),
                "single_bet_rq": m.get("single_bet_rq", False),
                "single_bet_goals": m.get("single_bet_goals", False),
                "handicap": m.get("handicap", 0), "odds": odds, "algo": algo,
                "spf_ev": spf_ev, "rq_ev": rq_ev, "goals_ev": goals_ev,
                "spf_mkt_implied": [round(p, 4) for p in spf_mkt]})
        return results

    def find_ev_bets(self, analyses):
        candidates = []
        for r in analyses:
            mid, home, away = r["id"], r["home"], r["away"]
            odds = r["odds"]
            # SPF single bets
            if r["single_bet"]:
                algo_probs = [r["algo"]["spf"]["H"], r["algo"]["spf"]["D"], r["algo"]["spf"]["A"]]
                for i in range(3):
                    ev = r["spf_ev"][i]
                    if ev > 0 and odds["spf"][i] > 0:
                        candidates.append(dict(match_id=mid, home=home, away=away, play_type="SPF",
                            selection=SPF_LABELS[i], selection_idx=i, odds=odds["spf"][i],
                            algo_prob=algo_probs[i], ev=ev, ev_pct=round(ev * 100, 1),
                            is_single=True, parlay_only=False,
                            desc=f"{mid} {home}vs{away} {SPF_LABELS[i]}"))
            # Total goals
            if r["single_bet_goals"] or r["single_bet"]:
                goals_odds = odds.get("goals", [0] * 8)
                for k in range(min(8, len(goals_odds))):
                    ev = r["goals_ev"].get(k, -1)
                    if ev > 0 and isinstance(goals_odds[k], (int, float)) and goals_odds[k] > 0:
                        candidates.append(dict(match_id=mid, home=home, away=away, play_type="总进球",
                            selection=GOALS_LABELS[k], selection_idx=k, odds=goals_odds[k],
                            algo_prob=r["algo"]["goals"].get(k, 0), ev=ev,
                            ev_pct=round(ev * 100, 1),
                            is_single=r["single_bet_goals"] or r["single_bet"],
                            parlay_only=False,
                            desc=f"{mid} {home}vs{away} {GOALS_LABELS[k]}"))
            # RQSPF (parlay only)
            if r["rq_ev"]:
                algo_rq = [r["algo"]["rqspf"]["fav2"], r["algo"]["rqspf"]["fav1"], r["algo"]["rqspf"]["dog"]]
                rq_odds = odds.get("rqspf", [0, 0, 0])
                for i in range(3):
                    ev = r["rq_ev"][i]
                    if ev > 0 and rq_odds[i] > 0:
                        candidates.append(dict(match_id=mid, home=home, away=away, play_type="RQSPF",
                            selection=RQSPF_LABELS[i], selection_idx=i, odds=rq_odds[i],
                            algo_prob=algo_rq[i], ev=ev, ev_pct=round(ev * 100, 1),
                            is_single=r.get("single_bet_rq", False),
                            parlay_only=True,
                            desc=f"{mid} RQ {RQSPF_LABELS[i]}"))
        candidates.sort(key=lambda x: x["ev_pct"], reverse=True)
        return candidates

    def allocate_budget(self, candidates):
        if not candidates:
            return []
        positive = [c for c in candidates if c["ev"] > 0]
        if not positive:
            return []
        weights = [max(0.1, c["ev_pct"]) * math.sqrt(max(0.01, c["algo_prob"])) for c in positive]
        total_w = sum(weights)
        if total_w <= 0:
            return []
        stakes = []
        for i, c in enumerate(positive):
            raw = weights[i] / total_w * self.budget
            zhu = max(1, round(raw / self.unit_price))
            stakes.append(zhu * self.unit_price)
        total_stake = sum(stakes)
        diff = int(self.budget - total_stake)
        if diff != 0:
            idxs = sorted(range(len(positive)), key=lambda i: weights[i], reverse=True)
            i_ptr = 0
            while diff != 0 and i_ptr < 200:
                idx = idxs[i_ptr % len(idxs)]
                if diff > 0:
                    stakes[idx] += self.unit_price
                    diff -= self.unit_price
                else:
                    if stakes[idx] >= self.unit_price * 2:
                        stakes[idx] -= self.unit_price
                        diff += self.unit_price
                i_ptr += 1
        result = []
        for i, c in enumerate(positive):
            zhu = int(stakes[i] / self.unit_price)
            result.append({**c, "stake": stakes[i], "zhu": zhu,
                "expected_return": round(stakes[i] * (1 + c["ev"]), 2)})
        result.sort(key=lambda x: (x["parlay_only"], -x["ev_pct"]))
        return result

    def generate_plan(self, matches):
        analyses = self.analyze_matches(matches)
        candidates = self.find_ev_bets(analyses)
        bets = self.allocate_budget(candidates)
        single_bets = [b for b in bets if not b["parlay_only"]]
        parlay_bets = [b for b in bets if b["parlay_only"]]
        total_stake = sum(b["stake"] for b in bets)
        total_return = sum(b["expected_return"] for b in bets)
        return dict(matches=matches, analyses=analyses,
            candidates_count=len(candidates), bets=bets,
            single_bets=single_bets, parlay_bets=parlay_bets,
            total_stake=total_stake, total_expected_return=round(total_return, 2),
            budget=self.budget, unit_price=self.unit_price)


def format_plan(plan):
    lines = []
    lines.append("=" * 72)
    lines.append("  竞彩足球智能分析投注方案")
    lines.append("  算法: Poisson(55%) + Elo(15%) + FIFA(30%) + 冷门因子")
    lines.append("=" * 72)
    lines.append("")
    lines.append("-" * 72)
    lines.append("【赛事对阵及赔率总览】")
    lines.append("-" * 72)
    for m in plan["matches"]:
        odds = m.get("odds", {})
        spf = odds.get("spf", [0, 0, 0])
        hcp = m.get("handicap", 0)
        hcp_str = f" 让球:{hcp:+d}" if hcp != 0 else ""
        single = "【单关】" if m.get("single_bet") else ""
        lines.append(f"  {m['id']} {m.get('league','')} {m.get('time','')}")
        lines.append(f"  {m['home']} vs {m['away']}{hcp_str} {single}")
        lines.append(f"  SPF赔率: 胜{spf[0]:.2f} / 平{spf[1]:.2f} / 负{spf[2]:.2f}")
        rq = odds.get("rqspf", [0, 0, 0])
        if rq and any(o > 0 for o in rq):
            lines.append(f"  RQ赔率: 胜{rq[0]:.2f} / 平{rq[1]:.2f} / 负{rq[2]:.2f}")
        lines.append("")
    lines.append("-" * 72)
    lines.append("【让球盘速查表】")
    lines.append("-" * 72)
    for a in plan["analyses"]:
        rq = a["algo"]["rqspf"]
        fav = "主" if rq["fav_is_home"] else "客"
        lines.append(f"  {a['id']} {a['home']}vs{a['away']}: 强队={fav} "
                     f"赢2+:{rq['fav2']*100:.1f}% 赢1:{rq['fav1']*100:.1f}% "
                     f"弱队不败:{rq['dog']*100:.1f}%")
    lines.append("")
    lines.append("-" * 72)
    lines.append("【单关/串关规则】")
    lines.append("-" * 72)
    lines.append("  · 单关: 仅标记【单关】的场次+玩法可单关投注")
    lines.append("  · 让球盘(RQSPF): 全部仅限串关")
    lines.append("  · 仅投算法概率 > 市场隐含概率的 +EV 选项")
    lines.append("")
    lines.append("-" * 72)
    lines.append("【投注明细表 (预期回报 = 本金 × (1+EV)，非最大回报)】")
    lines.append("-" * 72)
    header = f"  {'#':<4} {'类型':<6} {'场次':<8} {'投注内容':<22} {'金额':>6} {'注数':>4} {'赔率':>6} {'EV%':>7} {'期望回报':>8}"
    lines.append(header)
    lines.append("  " + "-" * 74)
    for i, b in enumerate(plan["bets"]):
        bet_type = "单关" if not b["parlay_only"] else "串关"
        lines.append(f"  {i+1:<4} {bet_type:<6} {b['match_id']:<8} {b['desc']:<22} "
                     f"{b['stake']:>5.0f}元 {b['zhu']:>3}注 {b['odds']:>5.2f} "
                     f"{b['ev_pct']:>6.1f}% {b['expected_return']:>7.0f}元")
    lines.append("  " + "-" * 74)
    ts = plan["total_stake"]
    tz = sum(b["zhu"] for b in plan["bets"])
    tr = plan["total_expected_return"]
    lines.append(f"  合计: {ts:.0f}元 / {tz}注 / 期望总回报: {tr:.0f}元")
    lines.append("")
    lines.append("-" * 72)
    lines.append("【场景推演】")
    lines.append("-" * 72)
    high_ev = sorted(plan["bets"], key=lambda x: x["ev_pct"], reverse=True)[:3]
    high_odds = sorted(plan["bets"], key=lambda x: x["odds"], reverse=True)[:3]
    lines.append(f"  ■ 最高EV: {', '.join(b['desc'] for b in high_ev)}")
    if high_odds:
        b = high_odds[0]
        max_ret = b["stake"] * b["odds"]
        lines.append(f"  ■ 高赔若中: {b['desc']} ({b['odds']:.1f}倍) → 回报{max_ret:.0f}元")
    lines.append("")
    lines.append("=" * 72)
    ev_total = sum(b["ev_pct"] * b["stake"] / ts for b in plan["bets"]) if ts > 0 else 0
    lines.append(f"  总投注: {ts:.0f}元 | 期望总回报: {tr:.0f}元 | 加权平均EV: {ev_total:.1f}%")
    lines.append(f"  候选项: {plan['candidates_count']} | 采纳: {len(plan['bets'])}")
    lines.append("=" * 72)
    return "\n".join(lines)
