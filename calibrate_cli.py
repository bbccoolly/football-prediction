"""
calibrate_cli.py -- 命令行校准工具
用法:
  python calibrate_cli.py              # 完整校准
  python calibrate_cli.py --quick      # 快速模式（仅用缓存数据）
  python calibrate_cli.py --report     # 只看上次报告
  python calibrate_cli.py --model elo  # 单独测试某个模型
"""

import sys, os, json, math, time, re, argparse
from datetime import datetime, timedelta
from collections import defaultdict
import requests
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/plain, */*",
}

# ========== 颜色输出 ==========
class Colors:
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    RED = "\033[91m"
    BLUE = "\033[94m"
    CYAN = "\033[96m"
    BOLD = "\033[1m"
    RESET = "\033[0m"
    DIM = "\033[2m"

def c(text, color=""):
    return f"{color}{text}{Colors.RESET}"

def bar(val, width=30, max_val=1.0):
    filled = int(val / max_val * width)
    if val > 0.7 * max_val: color = Colors.GREEN
    elif val > 0.4 * max_val: color = Colors.YELLOW
    else: color = Colors.RED
    return f"{color}{chr(9608)*filled}{Colors.DIM}{chr(9617)*(width-filled)}{Colors.RESET}"

def header(text):
    print(f"\n{c('='*70, Colors.DIM)}")
    print(f"  {c(text, Colors.BOLD + Colors.CYAN)}")
    print(f"{c('='*70, Colors.DIM)}")

def subheader(text):
    print(f"\n  {c(text, Colors.BOLD)}")

# ========== 数据抓取 ==========
def fetch_openligadb(league="bl1", season="2024", max_md=35):
    matches = []
    print(f"  {c('[OpenLigaDB]', Colors.DIM)} 抓取德甲 {season}...", end=" ", flush=True)
    for md in range(1, max_md):
        try:
            r = requests.get(f"https://api.openligadb.de/getmatchdata/{league}/{season}/{md}", headers=HEADERS, timeout=15)
            if r.status_code != 200: continue
            for m in r.json():
                if not m.get("matchResults") or len(m["matchResults"]) < 1: continue
                res = m["matchResults"][0]
                if res.get("pointsTeam1") is None: continue
                matches.append({
                    "home_team": m.get("team1",{}).get("teamName",""),
                    "away_team": m.get("team2",{}).get("teamName",""),
                    "home_goals": int(res["pointsTeam1"]),
                    "away_goals": int(res["pointsTeam2"]),
                    "league": "德甲", "date": m.get("matchDateTime","")[:10],
                })
            time.sleep(0.15)
        except: pass
    print(c(f"{len(matches)} 场", Colors.GREEN))
    return matches

def fetch_500_history(days_back=7):
    matches = []
    print(f"  {c('[500.com]', Colors.DIM)} 抓取近期完场...", end=" ", flush=True)
    for d in range(1, days_back+1):
        ds = (datetime.now() - timedelta(days=d)).strftime("%Y-%m-%d")
        try:
            r = requests.get(f"https://live.500.com/?e={ds}", headers=HEADERS, timeout=12)
            r.encoding = "gb2312"
            skip = ["退出","个人中心","全选","反选","设为首页","首页","开奖","登录","注册","比分","完","直播","待"]
            for row in re.findall(r'<tr[^>]*?>(.*?)</tr>', r.text, re.DOTALL):
                sm = re.search(r'(\d+)\s*-\s*(\d+)', row)
                if not sm: continue
                teams = re.findall(r'<a[^>]*?>([^<]{2,30})</a>', row)
                if len(teams) < 2: continue
                h, a = teams[0].strip(), teams[-1].strip()
                if any(w in h or w in a for w in skip): continue
                matches.append({"home_team":h,"away_team":a,"home_goals":int(sm.group(1)),"away_goals":int(sm.group(2)),"league":"","date":ds})
            time.sleep(0.3)
        except: pass
    print(c(f"{len(matches)} 场", Colors.GREEN))
    return matches

# ========== 回测核心 ==========
def run_backtest(matches, model_filter=None):
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
    train_n = max(10, n // 5)
    history = matches[:train_n]
    test = matches[train_n:]

    all_models = {
        "poisson": ("泊松分布", PoissonModel()),
        "dixon_coles": ("Dixon-Coles", DixonColesModel()),
        "elo": ("ELO 评级", EloRating()),
        "massey": ("Massey 排名", MasseyRanking()),
        "form": ("近期状态", FormModel()),
        "h2h": ("交锋记录", HeadToHeadModel()),
        "market": ("市场赔率", MarketOddsModel()),
        "knn": ("KNN 相似", KNNSimilarModel()),
        "xgboost": ("XGBoost", XGBoostModel()),
        "neural_net": ("神经网络", NeuralNetModel()),
        "monte_carlo": ("蒙特卡洛", MonteCarloModel(simulations=1000)),
        "bayesian": ("贝叶斯层次", BayesianHierarchicalModel()),
    }

    if model_filter:
        all_models = {k: v for k, v in all_models.items() if model_filter in k}

    # Init models with training data
    for key, (name, model) in all_models.items():
        if isinstance(model, EloRating):
            model.batch_update(history)
        elif isinstance(model, (PoissonModel, DixonColesModel)):
            s = build_strengths_from_results(history)
            model.set_team_strengths(s)
        elif isinstance(model, MasseyRanking):
            model.fit(history)
        elif isinstance(model, FormModel):
            model.load_history(history)
        elif isinstance(model, HeadToHeadModel):
            model.load_history(history)
        elif isinstance(model, BayesianHierarchicalModel):
            if len(history) >= 5: model.fit(history)

    # Results tracking
    results = {}
    for key in all_models:
        results[key] = {
            "total": 0, "correct": 0, "brier": 0, "log_loss": 0,
            "home_correct": 0, "home_total": 0,
            "draw_correct": 0, "draw_total": 0,
            "away_correct": 0, "away_total": 0,
            "goal_diff_errors": [],
        }

    print(f"\n  {c('回测中...', Colors.BOLD)} ({len(test)} 场)")
    bar_width = 40
    last_pct = -1

    for i, m in enumerate(test):
        home = m["home_team"]
        away = m["away_team"]
        actual = "H" if m["home_goals"] > m["away_goals"] else ("D" if m["home_goals"] == m["away_goals"] else "A")
        target = {"H": [1,0,0], "D": [0,1,0], "A": [0,0,1]}[actual]
        actual_gd = m["home_goals"] - m["away_goals"]

        preds = {}
        for key, (name, model) in all_models.items():
            try:
                if isinstance(model, EloRating):
                    preds[key] = model.predict_match(home, away, True)
                elif isinstance(model, MarketOddsModel):
                    preds[key] = model.predict()
                elif isinstance(model, KNNSimilarModel):
                    fv = model.feature_vector(1,1,1,1, 0.5,0.5, 1500,1500, 0)
                    preds[key] = model.predict(fv)
                elif isinstance(model, MonteCarloModel):
                    preds[key] = model.simulate(list(preds.values()), [1]*len(preds))
                else:
                    preds[key] = model.predict(home, away, True)
            except:
                continue

        for key, pred in preds.items():
            prob = [pred.get("home_win",0.33), pred.get("draw",0.34), pred.get("away_win",0.33)]
            brier = sum((p-t)**2 for p,t in zip(prob, target))
            ll = -math.log(max(prob[{"H":0,"D":1,"A":2}[actual]], 0.001))
            pred_r = "H" if prob[0] > max(prob[1], prob[2]) else ("D" if prob[1] > prob[2] else "A")
            results[key]["total"] += 1
            results[key]["brier"] += brier
            results[key]["log_loss"] += ll
            if pred_r == actual:
                results[key]["correct"] += 1
            results[key][f"{actual.lower()}_total"] += 1
            if pred_r == actual:
                results[key][f"{actual.lower()}_correct"] += 1
            # Goal diff error (from expected goals if available)
            if "expected_total_goals" in pred:
                eg = pred["expected_total_goals"]
                ag = m["home_goals"] + m["away_goals"]
                results[key]["goal_diff_errors"].append(abs(eg - ag))

        # Progress bar
        pct = (i+1)*100//len(test)
        if pct > last_pct:
            last_pct = pct
            done = (i+1)*bar_width//len(test)
            print(f"\r    [{c(chr(9608)*done, Colors.GREEN)}{c(chr(9617)*(bar_width-done), Colors.DIM)}] {pct:3d}%", end="", flush=True)

        # Rolling update
        history.append(m)
        s = build_strengths_from_results(history[-200:])
        for key, (name, model) in all_models.items():
            try:
                if isinstance(model, EloRating):
                    model.update(home, away, m["home_goals"], m["away_goals"], True)
                elif isinstance(model, (PoissonModel, DixonColesModel)):
                    model.set_team_strengths(s)
                elif isinstance(model, MasseyRanking):
                    model.fit(history[-200:])
                elif isinstance(model, FormModel):
                    model.load_history(history[-50:])
                elif isinstance(model, HeadToHeadModel):
                    model.load_history(history[-50:])
            except: pass

    print()
    return results

# ========== 报告输出 ==========
def print_report(results, total_matches):
    MODEL_NAMES = {
        "poisson":"泊松分布","dixon_coles":"Dixon-Coles","elo":"ELO 评级",
        "massey":"Massey 排名","form":"近期状态","h2h":"交锋记录",
        "market":"市场赔率","knn":"KNN 相似",
        "xgboost":"XGBoost","neural_net":"神经网络",
        "monte_carlo":"蒙特卡洛","bayesian":"贝叶斯层次",
    }

    header("回测报告")

    # 整体排名
    sorted_models = sorted(results.items(), key=lambda x: x[1]["correct"]/max(x[1]["total"],1), reverse=True)

    hdr_model = c('模型', Colors.BOLD)
    hdr_matches = c('场次', Colors.DIM)
    hdr_acc = c('准确率', Colors.DIM)
    hdr_brier = c('Brier', Colors.DIM)
    hdr_ll = c('LogLoss', Colors.DIM)
    hdr_hw = c('主胜率', Colors.DIM)
    hdr_draw = c('平局率', Colors.DIM)
    hdr_aw = c('客胜率', Colors.DIM)
    print(f"\n  {hdr_model:<16} {hdr_matches:>6} {hdr_acc:>9} {hdr_brier:>8} {hdr_ll:>8} {hdr_hw:>8} {hdr_draw:>8} {hdr_aw:>8}")
    print(f"  {c('-'*74, Colors.DIM)}")

    best_acc = 0
    best_brier = 999
    for key, r in sorted_models:
        if r["total"] == 0: continue
        acc = r["correct"] / r["total"]
        brier = r["brier"] / r["total"]
        ll = r["log_loss"] / r["total"]
        h_acc = r["home_correct"] / max(r["home_total"], 1)
        d_acc = r["draw_correct"] / max(r["draw_total"], 1)
        a_acc = r["away_correct"] / max(r["away_total"], 1)

        if acc > best_acc: best_acc = acc
        if brier < best_brier: best_brier = brier

        # Color by performance
        if acc >= 0.42: color = Colors.GREEN
        elif acc >= 0.36: color = Colors.YELLOW
        else: color = Colors.RED

        line = f"  {c(MODEL_NAMES.get(key,key), Colors.BOLD):<16} {c(str(r['total']),Colors.DIM):>6} "
        line += f"{c(f'{acc*100:5.1f}%',color):>9} {c(f'{brier:.4f}',Colors.DIM):>8} {c(f'{ll:.4f}',Colors.DIM):>8} "
        line += f"{c(f'{h_acc*100:4.0f}%',Colors.DIM):>8} {c(f'{d_acc*100:4.0f}%',Colors.DIM):>8} {c(f'{a_acc*100:4.0f}%',Colors.DIM):>8}"
        print(line)

    # 胜平负细分
    subheader("胜 / 平 / 负 预测细分")
    m1=c('模型',Colors.BOLD); m2=c('主胜预测',Colors.DIM); m3=c('平局预测',Colors.DIM); m4=c('客胜预测',Colors.DIM); m5=c('主胜实际',Colors.DIM); m6=c('平局实际',Colors.DIM); m7=c('客胜实际',Colors.DIM)
    print(f"  {m1:<16} {m2:>12} {m3:>12} {m4:>12} {m5:>12} {m6:>12} {m7:>12}")
    print(f"  {c('-'*78, Colors.DIM)}")
    for key, r in sorted_models:
        if r["total"] == 0: continue
        h_pred = sum(1 for _ in [1])  # Simplified - would need to track
        # Just show actual distribution
        nm = c(MODEL_NAMES.get(key,key), Colors.BOLD)
        ht = str(r.get("home_total","?"));  hc = str(r.get("home_correct","?"))
        dt = str(r.get("draw_total","?"));  dc = str(r.get("draw_correct","?"))
        at = str(r.get("away_total","?"));  ac = str(r.get("away_correct","?"))
        ch = "场"  # 场
        du = "对"  # 对
        print(f"  {nm:<16} {c(ht,Colors.DIM):>8}{ch} {c(hc,Colors.GREEN):>4}{du} {c(dt,Colors.DIM):>6}{ch} {c(dc,Colors.YELLOW):>4}{du} {c(at,Colors.DIM):>6}{ch} {c(ac,Colors.RED):>4}{du}")
    subheader("校准后的融合权重")
    scores = {}
    for key, r in results.items():
        if r["total"] < 3: continue
        scores[key] = max(0.05, 2.0 - r["brier"]/max(r["total"],1))
    total_s = sum(scores.values())
    weights = {}
    for key in scores:
        weights[key] = scores[key] / total_s

    sorted_w = sorted(weights.items(), key=lambda x: x[1], reverse=True)
    for key, w in sorted_w:
        print(f"  {c(MODEL_NAMES.get(key,key), Colors.BOLD):<16} {c(f'{w*100:5.1f}%', Colors.CYAN)}  {bar(w, 40, max(w for _,w in sorted_w))}")

    # 融合准确率估算
    subheader("融合效果估算")
    # 用校准后的权重加权平均
    weighted_acc = sum(results[k]["correct"]/max(results[k]["total"],1) * weights.get(k,0) for k in results if k in weights)
    baseline = 1/3
    print(f"  加权融合准确率: {c(f'{weighted_acc*100:.1f}%', Colors.GREEN)}")
    print(f"  随机基线:       {c(f'{baseline*100:.1f}%', Colors.DIM)}")
    print(f"  相对提升:       {c(f'+{(weighted_acc-baseline)*100:.1f}%', Colors.GREEN if weighted_acc > baseline else Colors.RED)}")
    print(f"  最佳单模型:     {c(f'{best_acc*100:.1f}%', Colors.CYAN)}")
    print(f"  测试总场次:     {c(str(total_matches), Colors.DIM)}")

    return weights

# ========== 保存 ==========
def save_calibration(results, weights, total_matches):
    from config import WEIGHTS_FILE
    report = {}
    for key, r in results.items():
        if r["total"] == 0: continue
        report[key] = {
            "matches": r["total"], "accuracy": round(r["correct"]/r["total"]*100, 1),
            "brier": round(r["brier"]/r["total"], 4), "log_loss": round(r["log_loss"]/r["total"], 4),
            "home_acc": round(r["home_correct"]/max(r["home_total"],1)*100, 1),
            "draw_acc": round(r["draw_correct"]/max(r["draw_total"],1)*100, 1),
            "away_acc": round(r["away_correct"]/max(r["away_total"],1)*100, 1),
        }

    os.makedirs(os.path.dirname(WEIGHTS_FILE), exist_ok=True)
    data = {"weights": {k: round(v,4) for k,v in weights.items()},
            "report": report, "total_matches": total_matches,
            "calibrated_at": datetime.now().isoformat()}
    with open(WEIGHTS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"\n  {c('✓', Colors.GREEN)} 权重已保存: {c(WEIGHTS_FILE, Colors.DIM)}")

    report_file = "data/processed/calibration_report.json"
    os.makedirs(os.path.dirname(report_file), exist_ok=True)
    with open(report_file, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  {c('✓', Colors.GREEN)} 报告已保存: {c(report_file, Colors.DIM)}")

# ========== 查看报告 ==========
def show_report():
    report_file = "data/processed/calibration_report.json"
    if not os.path.exists(report_file):
        print(f"\n  {c('✗ 未找到校准报告', Colors.RED)}")
        print(f"  {c('请先运行: python calibrate_cli.py', Colors.DIM)}")
        return

    with open(report_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    header("上次校准报告")
    print(f"  校准时间: {c(data.get('calibrated_at','?')[:19], Colors.DIM)}")
    print(f"  比赛场次: {c(str(data.get('total_matches','?')), Colors.BOLD)}")

    report = data.get("report", {})
    weights = data.get("weights", {})
    MODEL_NAMES = {"poisson":"泊松分布","dixon_coles":"Dixon-Coles","elo":"ELO 评级","massey":"Massey 排名","form":"近期状态","h2h":"交锋记录","market":"市场赔率","knn":"KNN 相似","xgboost":"XGBoost","neural_net":"神经网络","monte_carlo":"蒙特卡洛","bayesian":"贝叶斯层次"}

    r1=c('模型',Colors.BOLD); r2=c('准确率',Colors.DIM); r3=c('Brier',Colors.DIM); r4=c('权重',Colors.DIM)
    print(f"\n  {r1:<16} {r2:>7} {r3:>8} {r4:>8}")
    print(f"  {c('-'*42, Colors.DIM)}")
    for key in sorted(report, key=lambda k: report[k].get("accuracy",0), reverse=True):
        r = report[key]
        w = weights.get(key, 0)
        acc = r.get("accuracy", 0)
        if acc >= 42: color = Colors.GREEN
        elif acc >= 36: color = Colors.YELLOW
        else: color = Colors.RED
        nm = c(MODEL_NAMES.get(key,key), Colors.BOLD)
        ac = c(f"{acc:5.1f}%", color)
        br = c(f"{r.get("brier",0):.4f}", Colors.DIM)
        wt = c(f"{w*100:5.1f}%", Colors.CYAN)
        print(f"  {nm:<16} {ac:>7} {br:>8} {wt:>8}")

# ========== 主入口 ==========
def main():
    parser = argparse.ArgumentParser(description="足球预测系统 - 算法校准 CLI")
    parser.add_argument("--quick", action="store_true", help="快速模式（仅用缓存数据）")
    parser.add_argument("--report", action="store_true", help="仅查看上次校准报告")
    parser.add_argument("--model", type=str, help="仅测试指定模型 (如 elo, poisson)")
    parser.add_argument("--days", type=int, default=7, help="500.com 抓取天数 (默认7)")
    args = parser.parse_args()

    if args.report:
        show_report()
        return

    header("足球预测系统 — 算法校准 CLI")
    print(f"  时间: {c(datetime.now().strftime('%Y-%m-%d %H:%M:%S'), Colors.DIM)}")
    print(f"  模式: {c('快速' if args.quick else '完整', Colors.YELLOW)}")
    if args.model: print(f"  模型: {c(args.model, Colors.CYAN)}")

    # 收集数据
    all_matches = []

    if not args.quick:
        header("数据抓取")
        for code, name, max_md in [("bl1","德甲",35), ("bl2","德乙",35), ("bl3","德丙",39)]:
            for season in ["2024", "2023", "2022"]:
                try:
                    all_matches.extend(fetch_openligadb(code, season, max_md))
                except: pass
        all_matches.extend(fetch_500_history(args.days))

        # 去重
        seen = set()
        unique = []
        for m in all_matches:
            key = f"{m['home_team']}|{m['away_team']}|{m.get('date','')}"
            if key not in seen:
                seen.add(key)
                unique.append(m)
        all_matches = unique
    else:
        # 用缓存
        cache_file = "data/raw/matches_cache.json"
        if os.path.exists(cache_file):
            with open(cache_file, "r", encoding="utf-8") as f:
                all_matches = json.load(f).get("history", [])
        if not all_matches:
            # Fallback to calibration report's data... we need data
            print(f"  {c('无缓存数据，切换到完整模式', Colors.YELLOW)}")
            all_matches = fetch_openligadb("bl1", "2024")

    if len(all_matches) < 20:
        print(f"\n  {c('✗ 数据不足 (需至少20场，当前{})'.format(len(all_matches)), Colors.RED)}")
        print(f"  {c('请检查网络连接后重试', Colors.DIM)}")
        return

    print(f"\n  {c('总计', Colors.BOLD)}: {len(all_matches)} 场历史比赛")

    # 回测
    results = run_backtest(all_matches, args.model)

    # 报告
    weights = print_report(results, len(all_matches))

    # 保存
    if weights and not args.model:
        save_calibration(results, weights, len(all_matches))

    print()

if __name__ == "__main__":
    main()
