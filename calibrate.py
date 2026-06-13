"""
calibrate.py -- 抓取历史数据 + 回测校准 12 个算法
"""

import sys, os, json, math, time, re
from datetime import datetime, timedelta
import requests
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}

# ======================================================================
# Step 1: 抓取历史比赛数据
# ======================================================================
def fetch_openligadb(league="bl1", season="2024"):
    """从 OpenLigaDB 抓取历史数据（免费 API，支持德甲/德乙/德丙）"""
    matches = []
    max_md = 35 if league == "bl1" else (35 if league == "bl2" else 39)
    for matchday in range(1, max_md):
        try:
            url = f"https://api.openligadb.de/getmatchdata/{league}/{season}/{matchday}"
            r = requests.get(url, headers=HEADERS, timeout=15)
            if r.status_code != 200:
                continue
            data = r.json()
            for m in data:
                if not m.get("matchResults") or len(m["matchResults"]) < 1:
                    continue
                result = m["matchResults"][0]
                if result.get("pointsTeam1") is None:
                    continue
                hg = result.get("pointsTeam1", 0)
                ag = result.get("pointsTeam2", 0)
                matches.append({
                    "home_team": m.get("team1", {}).get("teamName", ""),
                    "away_team": m.get("team2", {}).get("teamName", ""),
                    "home_goals": int(hg),
                    "away_goals": int(ag),
                    "league": "德甲",
                    "date": m.get("matchDateTime", "")[:10],
                    "match_id": str(m.get("matchID", "")),
                })
            time.sleep(0.3)
        except Exception as e:
            print(f"  matchday {matchday}: {e}")
    print(f"  [OpenLigaDB] {len(matches)} matches")
    return matches

def fetch_500_history(date_str=None):
    """从 500.com 抓取历史完场比赛"""
    if date_str is None:
        date_str = (datetime.now() - timedelta(days=2)).strftime("%Y-%m-%d")
    matches = []
    try:
        url = f"https://live.500.com/?e={date_str}"
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.encoding = "gb2312"
        html = r.text

        # 找比赛表格行
        rows = re.findall(r'<tr[^>]*?class="[^"]*?(?:live|over|schedule)[^"]*?"[^>]*?>(.*?)</tr>', html, re.DOTALL)
        if not rows:
            # 尝试更通用的匹配
            rows = re.findall(r'<tr[^>]*?>(.*?)</tr>', html, re.DOTALL)

        for row in rows:
            # 找比分 如 "1-0", "2-1"
            score_match = re.search(r'(\d+)\s*-\s*(\d+)', row)
            if not score_match:
                continue
            hg = int(score_match.group(1))
            ag = int(score_match.group(2))

            # 找球队名
            teams = re.findall(r'<a[^>]*?>([^<]{2,30})</a>', row)
            if len(teams) < 2:
                continue

            # 过滤非球队文字
            skip = ["退出","个人中心","全选","反选","设为首页","首页","开奖","登录","注册","比分","完","直播","待"]
            home = teams[0].strip()
            away = teams[-1].strip()
            if any(w in home or w in away for w in skip):
                continue

            matches.append({
                "home_team": home, "away_team": away,
                "home_goals": hg, "away_goals": ag,
                "league": "",
                "date": date_str,
            })

        print(f"  [500.com {date_str}] {len(matches)} matches")
    except Exception as e:
        print(f"  [500.com {date_str}]: {e}")
    return matches

# ======================================================================
# Step 2: 收集全部历史数据
# ======================================================================
def collect_all_history():
    """收集所有可用的历史比赛数据"""
    all_matches = []

    # OpenLigaDB: 德甲 + 德乙 + 德丙, 多赛季
    for league_code, league_name in [("bl1","德甲"), ("bl2","德乙"), ("bl3","德丙")]:
        for season in ["2024", "2023", "2022"]:
            print(f"[收集] OpenLigaDB {league_name} {season}...")
            try:
                m = fetch_openligadb(league_code, season)
                all_matches.extend(m)
            except Exception as e:
                print(f"  跳过: {e}")
            time.sleep(0.5)

    # 500.com 更长时间范围
    print("[收集] 500.com 近期比赛...")
    for days_ago in range(1, 15):
        d = (datetime.now() - timedelta(days=days_ago)).strftime("%Y-%m-%d")
        m = fetch_500_history(d)
        if m:
            all_matches.extend(m)
        time.sleep(0.3)

    # 去重
    seen = set()
    unique = []
    for m in all_matches:
        key = f"{m['home_team']}|{m['away_team']}|{m.get('date','')}"
        if key not in seen:
            seen.add(key)
            unique.append(m)

    print(f"\n[收集] 总计 {len(unique)} 场历史比赛")
    return unique

# ======================================================================
# Step 3: 回测
# ======================================================================
def backtest(matches):
    """对每场比赛运行 12 个算法，对比预测 vs 实际"""
    from models.elo import EloRating
    from models.poisson import PoissonModel, build_strengths_from_results
    from models.dixon_coles import DixonColesModel
    from models.massey import MasseyRanking
    from models.form import FormModel
    from models.head_to_head import HeadToHeadModel
    from models.market_odds import MarketOddsModel
    from models.knn_similar import KNNSimilarModel
    from models.monte_carlo import MonteCarloModel
    from models.bayesian_hierarchical import BayesianHierarchicalModel
    from models.xgboost_model import XGBoostModel
    from models.neural_net import NeuralNetModel

    n = len(matches)
    print(f"\n[回测] {n} 场比赛，逐场滚动预测...")

    # 用前 20% 数据作为初始训练集
    train_size = max(10, n // 5)
    history = matches[:train_size]
    test = matches[train_size:]

    # 初始化模型
    elo = EloRating()
    strengths = build_strengths_from_results(history)
    poisson = PoissonModel(); poisson.set_team_strengths(strengths)
    dc = DixonColesModel(); dc.set_team_strengths(strengths)
    massey = MasseyRanking(); massey.fit(history)
    form = FormModel(); form.load_history(history)
    h2h = HeadToHeadModel(); h2h.load_history(history)
    market = MarketOddsModel()
    knn = KNNSimilarModel()
    for m in history:
        fv = knn.feature_vector(1,1,1,1, 0.5,0.5, 1500,1500, 0)
        knn.add_match(fv, m["home_goals"], m["away_goals"])
    xgb = XGBoostModel()
    nn = NeuralNetModel()
    mc = MonteCarloModel(simulations=1000)
    bayes = BayesianHierarchicalModel()
    if len(history) >= 5:
        bayes.fit(history)

    results = {name: {"brier": 0, "correct": 0, "total": 0, "log_loss": 0}
               for name in ["poisson","dixon_coles","elo","massey","form","head_to_head",
                            "market_odds","knn","xgboost","neural_net","monte_carlo","bayesian"]}

    for i, m in enumerate(test):
        home = m["home_team"]
        away = m["away_team"]
        actual = "H" if m["home_goals"] > m["away_goals"] else ("D" if m["home_goals"] == m["away_goals"] else "A")
        target = {"H": [1,0,0], "D": [0,1,0], "A": [0,0,1]}[actual]

        preds = {}
        try:
            preds["poisson"] = poisson.predict(home, away, True)
            preds["dixon_coles"] = dc.predict(home, away, True)
            preds["elo"] = elo.predict_match(home, away, True)
            preds["massey"] = massey.predict(home, away, True)
            preds["form"] = form.predict(home, away, True)
            preds["head_to_head"] = h2h.predict(home, away, True)
            preds["market_odds"] = market.predict()
            fq = knn.feature_vector(1,1,1,1, form.get_form_score(home)["form_score"], form.get_form_score(away)["form_score"],
                                    elo.get_rating(home), elo.get_rating(away),
                                    elo.get_rating(home)-elo.get_rating(away))
            preds["knn"] = knn.predict(fq)
            preds["xgboost"] = xgb.predict(fq)
            preds["neural_net"] = nn.predict(fq)
            preds["monte_carlo"] = mc.simulate(list(preds.values()), [1]*len(preds))
            preds["bayesian"] = bayes.predict(home, away, True)
        except Exception as e:
            continue

        for name, pred in preds.items():
            prob = [pred.get("home_win", 0.33), pred.get("draw", 0.34), pred.get("away_win", 0.33)]
            brier = sum((p - t)**2 for p, t in zip(prob, target))
            pred_result = "H" if prob[0] > max(prob[1], prob[2]) else ("D" if prob[1] > prob[2] else "A")
            correct = 1 if pred_result == actual else 0
            ll = -math.log(max(prob[{"H":0,"D":1,"A":2}[actual]], 0.001))

            results[name]["brier"] += brier
            results[name]["correct"] += correct
            results[name]["total"] += 1
            results[name]["log_loss"] += ll

        # 更新模型（滚动：用新比赛更新 ELO 等）
        elo.update(home, away, m["home_goals"], m["away_goals"], neutral=True)
        history.append(m)
        strengths = build_strengths_from_results(history[-200:])
        poisson.set_team_strengths(strengths)
        dc.set_team_strengths(strengths)
        massey.fit(history[-200:])
        form.load_history(history[-50:])
        h2h.load_history(history[-50:])
        knn.add_match(fq, m["home_goals"], m["away_goals"])
        if len(history[-100:]) >= 5:
            bayes.fit(history[-100:])

        if (i+1) % 20 == 0:
            print(f"  进度: {i+1}/{len(test)}")

    # 汇总
    print(f"\n{'='*70}")
    print(f"{'模型':<16} {'场次':>6} {'准确率':>8} {'Brier':>8} {'LogLoss':>8}")
    print(f"{'-'*70}")
    report = {}
    for name in results:
        r = results[name]
        if r["total"] == 0:
            continue
        acc = r["correct"] / r["total"] * 100
        brier = r["brier"] / r["total"]
        ll = r["log_loss"] / r["total"]
        print(f"{name:<16} {r['total']:>6} {acc:>7.1f}% {brier:>8.4f} {ll:>8.4f}")
        report[name] = {"matches": r["total"], "accuracy": round(acc,1), "brier": round(brier,4), "log_loss": round(ll,4)}

    return report

# ======================================================================
# Step 4: 校准权重
# ======================================================================
def calibrate_weights(report):
    """根据回测结果重新计算权重（Brier 越小 → 权重越大）"""
    scores = {}
    for name, r in report.items():
        if r["matches"] < 3:
            continue
        # Brier Score 范围 0-2，好的约 0.5，差的约 1.5
        # 转换为分数：2 - brier，再 softmax
        scores[name] = max(0.1, 2.0 - r["brier"])

    total = sum(scores.values())
    if total == 0:
        return None

    weights = {}
    for name in scores:
        weights[name] = round(scores[name] / total, 4)

    return weights

# ======================================================================
# Main
# ======================================================================
def main():
    print("=" * 60)
    print("  足球预测系统 — 算法校准")
    print("=" * 60)

    # 收集数据
    matches = collect_all_history()

    if len(matches) < 20:
        print("\n[!] 历史数据不足（需至少 20 场），无法校准。")
        print("   当前网络环境限制了数据抓取。")
        return

    # 回测
    report = backtest(matches)

    # 校准
    weights = calibrate_weights(report)
    if weights:
        print(f"\n{'='*70}")
        print(f"  校准后的融合权重")
        print(f"{'-'*70}")
        for name, w in sorted(weights.items(), key=lambda x: x[1], reverse=True):
            bar = "#" * int(w * 100)
            print(f"  {name:<16} {w*100:5.1f}%  {bar}")

        # 保存
        from config import WEIGHTS_FILE
        os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
        with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
            json.dump({"weights": weights, "calibrated_at": datetime.now().isoformat(),
                        "matches_used": len(matches), "report": report}, f, ensure_ascii=False, indent=2)
        print(f"\n  权重已保存到 {WEIGHTS_FILE}")

    # 保存回测报告
    report_file = "data/processed/calibration_report.json"
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump({"report": report, "weights": weights, "total_matches": len(matches),
                    "calibrated_at": datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
    print(f"  回测报告已保存到 {report_file}")

    return report, weights

if __name__ == "__main__":
    main()
