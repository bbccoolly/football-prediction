import sys, os, json, math, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import numpy as np

from models.elo import EloRating
from models.poisson import PoissonModel, build_strengths_from_results
from models.dixon_coles import DixonColesModel
from models.massey import MasseyRanking
from models.form import FormModel
from models.head_to_head import HeadToHeadModel
from models.market_odds import MarketOddsModel
from models.knn_similar import KNNSimilarModel
from models.xgboost_model import XGBoostModel
from models.neural_net import NeuralNetModel
from models.monte_carlo import MonteCarloModel
from models.bayesian_hierarchical import BayesianHierarchicalModel
from features.player_impact import PlayerImpact
from features.builder import FeatureBuilder
from ensemble.bma import BayesianModelAveraging
from ensemble.stacker import StackingEnsemble
from config import *

app = Flask(__name__)

def _convert_numpy(obj):
    if isinstance(obj, dict):
        return {int(k) if isinstance(k, np.integer) else float(k) if isinstance(k, np.floating) else k: _convert_numpy(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)): return [_convert_numpy(x) for x in obj]
    if isinstance(obj, np.integer): return int(obj)
    if isinstance(obj, np.floating): return float(obj)
    if isinstance(obj, np.ndarray): return obj.tolist()
    return obj

_prediction_progress = {}

elo_model = EloRating()
poisson_model = PoissonModel()
dixon_coles_model = DixonColesModel()
massey_model = MasseyRanking()
form_model = FormModel()
h2h_model = HeadToHeadModel()
market_model = MarketOddsModel()
knn_model = KNNSimilarModel()
xgb_model = XGBoostModel()
nn_model = NeuralNetModel()
mc_model = MonteCarloModel()
bayes_model = BayesianHierarchicalModel()
player_impact = PlayerImpact()
bma = BayesianModelAveraging()
stacker = StackingEnsemble()
feature_builder = FeatureBuilder()

_upcoming_matches_cache = []
_fetch_errors = []
_teams_cache = list(ALL_TEAMS)
_initialized = False

def _init_models():
    global _upcoming_matches_cache, _fetch_errors, _teams_cache, _initialized
    if _initialized: return

    elo_model.load()

    # 从持久化历史数据库加载真实比赛数据
    try:
        from data.history_db import load_history
        history = load_history()
        print(f"[Init] history DB: {len(history)} matches")
    except Exception as e:
        print(f"[Init] history load failed: {e}")
        history = []

    # 线上抓取待开赛比赛
    try:
        from data.fetcher import load_or_fetch
        data = load_or_fetch()
        upcoming = data.get("upcoming", [])
        _fetch_errors = data.get("errors", [])
        _upcoming_matches_cache = upcoming
        extra = set()
        for m in upcoming:
            for k in ["home_team", "away_team"]:
                t = m.get(k, "")
                if t: extra.add(t)
        _teams_cache = sorted(set(_teams_cache) | extra)
    except Exception as e:
        print(f"[Init] fetch: {e}")
        upcoming = []
        _fetch_errors = [str(e)]
        _upcoming_matches_cache = []

    # 用历史数据初始化所有模型
    if history:
        strengths = build_strengths_from_results(history)
        poisson_model.set_team_strengths(strengths)
        dixon_coles_model.set_team_strengths(strengths)
        massey_model.fit(history)
        form_model.load_history(history)
        h2h_model.load_history(history)
        elo_model.batch_update(history)
        elo_model.save()
        for m in history[:100]:
            fv = knn_model.feature_vector(1.0,1.0,1.0,1.0,
                form_model.get_form_score(m["home_team"])["form_score"],
                form_model.get_form_score(m["away_team"])["form_score"],
                elo_model.get_rating(m["home_team"]), elo_model.get_rating(m["away_team"]),
                elo_model.get_rating(m["home_team"])-elo_model.get_rating(m["away_team"]))
            knn_model.add_match(fv, m.get("home_goals",0), m.get("away_goals",0))
        bayes_model.fit(history)

    for team, players in SAMPLE_PLAYERS.items():
        player_impact.set_squad(team, players)

    bma.load()
    xgb_model.load()
    nn_model.load()
    stacker.load()

    _initialized = True
    print(f"[Init] {len(history)} matches, {len(_teams_cache)} teams, {len(_upcoming_matches_cache)} upcoming")
@app.route("/")
def index():
    _init_models()
    return render_template("index.html",
                          national_teams=NATIONAL_TEAMS,
                          club_teams=CLUB_TEAMS,
                          all_teams=_teams_cache,
                          players=SAMPLE_PLAYERS,
                          leagues=list(LEAGUES.keys()),
                          upcoming=_upcoming_matches_cache[:50],
                          fetch_errors=_fetch_errors)

@app.route("/api/status")
def api_status():
    """返回数据抓取状态"""
    _init_models()
    return jsonify({
        "upcoming_count": len(_upcoming_matches_cache),
        "teams_count": len(_teams_cache),
        "fetch_errors": _fetch_errors,
        "data_available": len(_upcoming_matches_cache) > 0,
    })

@app.route("/api/progress/<task_id>")
def progress(task_id):
    def generate():
        last = ""
        while True:
            info = _prediction_progress.get(task_id, {})
            current = json.dumps(info)
            if current != last:
                last = current
                yield f"data: {current}\n\n"
            if info.get("done", 0) >= info.get("total", 12):
                break
            time.sleep(0.15)
        _prediction_progress.pop(task_id, None)
    return Response(stream_with_context(generate()), mimetype="text/event-stream",
                    headers={"Cache-Control":"no-cache","X-Accel-Buffering":"no"})

@app.route("/api/search_matches")
def api_search_matches():
    _init_models()
    team_a = request.args.get("team_a", "").strip()
    team_b = request.args.get("team_b", "").strip()

    if not team_a or not team_b:
        return jsonify({"matches": [], "error": "请指定两支球队"})

    results = []

    # 1. 搜索线上抓取到的比赛
    for m in _upcoming_matches_cache:
        ht = m.get("home_team", "")
        at = m.get("away_team", "")
        if (team_a in ht and team_b in at) or (team_a in at and team_b in ht):
            results.append({
                "home_team": ht, "away_team": at,
                "league": m.get("league", ""),
                "date": m.get("date", "") or m.get("match_time", ""),
                "home_odds": m.get("home_odds"),
                "draw_odds": m.get("draw_odds"),
                "away_odds": m.get("away_odds"), "venue": m.get("venue",""),
                "home_goals": m.get("home_goals"),
                "away_goals": m.get("away_goals"),
            })

    # 2. 搜索历史交锋记录
    if not results:
        h2h = h2h_model.get_h2h(team_a, team_b)
        if h2h.get("total_matches", 0) > 0:
            results.append({
                "home_team": team_a, "away_team": team_b,
                "league": "历史交锋",
                "date": f"共 {h2h['total_matches']} 场",
                "home_goals": h2h.get("a_goals", 0),
                "away_goals": h2h.get("b_goals", 0),
                "note": f"主队视角: {h2h.get('a_wins',0)}胜{h2h.get('draws',0)}平{h2h.get('b_wins',0)}负",
            })

    # 3. 搜各自近期比赛
    team_a_matches = []
    team_b_matches = []
    for m in _upcoming_matches_cache:
        mt = m.get("home_team", "") + " " + m.get("away_team", "")
        if team_a in mt and team_a not in [r.get("home_team","") for r in team_a_matches]:
            team_a_matches.append(m)
        if team_b in mt:
            team_b_matches.append(m)

    return jsonify({
        "matches": results,
        "count": len(results),
        "team_a_matches": [{
            "home_team": m.get("home_team",""), "away_team": m.get("away_team",""),
            "league": m.get("league",""), "date": m.get("date","") or m.get("match_time",""),
            "home_odds": m.get("home_odds"), "draw_odds": m.get("draw_odds"), "away_odds": m.get("away_odds"), "venue": m.get("venue",""),
        } for m in team_a_matches[:5]],
        "team_b_matches": [{
            "home_team": m.get("home_team",""), "away_team": m.get("away_team",""),
            "league": m.get("league",""), "date": m.get("date","") or m.get("match_time",""),
            "home_odds": m.get("home_odds"), "draw_odds": m.get("draw_odds"), "away_odds": m.get("away_odds"), "venue": m.get("venue",""),
        } for m in team_b_matches[:5]],
        "data_available": len(_upcoming_matches_cache) > 0,
    })

@app.route("/predict", methods=["POST"])
def predict():
    _init_models()
    data = request.get_json()
    home_team = data.get("home_team", "").strip()
    away_team = data.get("away_team", "").strip()
    league_cn = data.get("league", "世界杯")
    league = LEAGUES.get(league_cn, "world_cup")
    neutral = data.get("neutral", True)
    home_missing = data.get("home_missing", [])
    away_missing = data.get("away_missing", [])
    home_odds = data.get("home_odds")
    draw_odds = data.get("draw_odds")
    away_odds = data.get("away_odds")
    task_id = data.get("task_id", "default")

    if not home_team or not away_team:
        return jsonify({"error":"请选择主队和客队"}), 400
    if home_team == away_team:
        return jsonify({"error":"主客队不能相同"}), 400

    _prediction_progress[task_id] = {"total":12,"done":0,"current":"准备中..."}
    def report(n,name):
        if task_id in _prediction_progress:
            _prediction_progress[task_id]["done"]=n
            _prediction_progress[task_id]["current"]=name

    for team, missing in [(home_team, home_missing), (away_team, away_missing)]:
        if team in SAMPLE_PLAYERS: player_impact.set_squad(team, SAMPLE_PLAYERS[team])
        player_impact.set_injuries(team, missing)
    squad_info = player_impact.both_teams_impact(home_team, away_team)
    home_adv = HOME_ADVANTAGE.get(league_cn, HOME_ADVANTAGE.get(league, 0.35))

    predictions = {}
    predictions["poisson"] = poisson_model.predict(home_team, away_team, neutral); report(1,"泊松分布")
    predictions["htft"] = poisson_model.predict_htft(home_team, away_team, neutral)
    htft_result = predictions.pop("htft")  # keep separate, not in model loop
    predictions["dixon_coles"] = dixon_coles_model.predict(home_team, away_team, neutral); report(2,"Dixon-Coles")
    predictions["elo"] = elo_model.predict_match(home_team, away_team, neutral); report(3,"ELO评级")
    predictions["massey"] = massey_model.predict(home_team, away_team, neutral); report(4,"Massey排名")
    predictions["form"] = form_model.predict(home_team, away_team, neutral); report(5,"近期状态")
    predictions["head_to_head"] = h2h_model.predict(home_team, away_team, neutral); report(6,"交锋记录")

    if home_odds and draw_odds and away_odds:
        try:
            predictions["market_odds"] = market_model.predict(home_odds=float(home_odds), draw_odds=float(draw_odds), away_odds=float(away_odds))
        except: predictions["market_odds"] = market_model.predict()
    else:
        predictions["market_odds"] = market_model.predict()
    report(7,"市场赔率")

    fq = knn_model.feature_vector(
        poisson_model.attack_strengths.get(home_team,1.0), poisson_model.defense_strengths.get(home_team,1.0),
        poisson_model.attack_strengths.get(away_team,1.0), poisson_model.defense_strengths.get(away_team,1.0),
        form_model.get_form_score(home_team)["form_score"], form_model.get_form_score(away_team)["form_score"],
        elo_model.get_rating(home_team), elo_model.get_rating(away_team),
        elo_model.get_rating(home_team)-elo_model.get_rating(away_team))
    predictions["knn_similar"] = knn_model.predict(fq); report(8,"KNN相似")

    fb = feature_builder.build(
        elo_home=elo_model.get_rating(home_team), elo_away=elo_model.get_rating(away_team),
        atk_home=poisson_model.attack_strengths.get(home_team,1.0), atk_away=poisson_model.attack_strengths.get(away_team,1.0),
        def_home=poisson_model.defense_strengths.get(home_team,1.0), def_away=poisson_model.defense_strengths.get(away_team,1.0),
        form_home=form_model.get_form_score(home_team), form_away=form_model.get_form_score(away_team),
        h2h_stats=h2h_model.get_h2h(home_team, away_team),
        squad_home=squad_info["home_completeness"], squad_away=squad_info["away_completeness"],
        home_adv=home_adv, neutral=neutral)
    predictions["xgboost"] = xgb_model.predict(fb["vector"]); report(9,"XGBoost")
    predictions["neural_net"] = nn_model.predict(fb["vector"]); report(10,"神经网络")

    preds_mc = [v for v in predictions.values()]
    w_mc = [bma.get_weights().get(k,0.08) for k in predictions.keys()]
    predictions["monte_carlo"] = mc_model.simulate(preds_mc, w_mc); report(11,"蒙特卡洛")
    predictions["bayesian"] = bayes_model.predict(home_team, away_team, neutral); report(12,"贝叶斯层次")

    handicap_result = poisson_model.predict_handicap(home_team, away_team, neutral)
    blend_result = bma.blend(predictions)

    home_probs = [p["home_win"] for p in predictions.values()]
    draw_probs = [p["draw"] for p in predictions.values()]
    away_probs = [p["away_win"] for p in predictions.values()]
    std_h = math.sqrt(sum((x-sum(home_probs)/len(home_probs))**2 for x in home_probs)/len(home_probs))
    std_d = math.sqrt(sum((x-sum(draw_probs)/len(draw_probs))**2 for x in draw_probs)/len(draw_probs))
    std_a = math.sqrt(sum((x-sum(away_probs)/len(away_probs))**2 for x in away_probs)/len(away_probs))
    confidence = max(0, min(100, round(100*(1.0-(std_h+std_d+std_a)/3*5), 1)))

    return jsonify(_convert_numpy({
        "home_team":home_team,"away_team":away_team,
        "neutral":neutral,"league":league,
        "squad_info":squad_info,
        "predictions":predictions,
        "htft":htft_result,
        "ensemble":blend_result,
        "handicap":handicap_result,
        "confidence":confidence,
    }))

@app.route("/api/upcoming")
def api_upcoming():
    return jsonify({"upcoming":_upcoming_matches_cache[:80],"count":len(_upcoming_matches_cache),"data_available":len(_upcoming_matches_cache)>0})

@app.route("/api/refresh_data")
def api_refresh_data():
    global _upcoming_matches_cache, _teams_cache, _fetch_errors
    try:
        from data.fetcher import load_or_fetch
        d = load_or_fetch(force_refresh=True)
        _upcoming_matches_cache = d.get("upcoming",[])
        _fetch_errors = d.get("errors", [])
        extra = set()
        for m in _upcoming_matches_cache:
            for k in ["home_team","away_team"]:
                t = m.get(k,""); 
                if t: extra.add(t)
        _teams_cache = sorted(set(_teams_cache)|extra)
        success = len(_upcoming_matches_cache) > 0
        return jsonify({"status":"ok" if success else "no_data","upcoming":len(_upcoming_matches_cache),"teams":len(_teams_cache),"errors":_fetch_errors})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}),500


@app.route("/api/debug_predict", methods=["POST"])
def api_debug_predict():
    """返回完整计算过程"""
    _init_models()
    data = request.get_json()
    home_team = data.get("home_team", "").strip()
    away_team = data.get("away_team", "").strip()
    neutral = data.get("neutral", False)

    if not home_team or not away_team:
        return jsonify({"error": "need teams"}), 400

    debug = {"home_team": home_team, "away_team": away_team, "neutral": neutral}

    # ---- 原始数据 ----
    debug["raw_data"] = {
        "elo_home": elo_model.get_rating(home_team),
        "elo_away": elo_model.get_rating(away_team),
        "attack_home": poisson_model.attack_strengths.get(home_team, 1.0),
        "defense_home": poisson_model.defense_strengths.get(home_team, 1.0),
        "attack_away": poisson_model.attack_strengths.get(away_team, 1.0),
        "defense_away": poisson_model.defense_strengths.get(away_team, 1.0),
        "form_home": form_model.get_form_score(home_team),
        "form_away": form_model.get_form_score(away_team),
        "h2h": h2h_model.get_h2h(home_team, away_team),
        "massey_home": massey_model.ratings.get(home_team, 0),
        "massey_away": massey_model.ratings.get(away_team, 0),
    }

    # ---- 每个模型的逐步计算 ----
    steps = {}

    # Poisson
    la = debug["raw_data"]["attack_home"]
    ld = debug["raw_data"]["defense_home"]
    ra = debug["raw_data"]["attack_away"]
    rd = debug["raw_data"]["defense_away"]
    hf = 1.0 if neutral else 1.15
    lam_h = 1.35 * la * rd * hf
    lam_a = 1.35 * ra * ld * (1.0/hf)
    steps["poisson"] = {
        "formula": "lambda = league_avg/2 * attack * opp_defense * home_factor",
        "league_avg_half": 1.35,
        "home_attack_used": la, "away_defense_used": rd, "home_factor": hf,
        "away_attack_used": ra, "home_defense_used": ld,
        "lambda_home": round(lam_h, 3), "lambda_away": round(lam_a, 3),
        "expected_total": round(lam_h + lam_a, 3),
        "interpretation": f"主队预期进 {lam_h:.2f} 球, 客队预期进 {lam_a:.2f} 球",
    }

    # ELO
    eh = debug["raw_data"]["elo_home"]
    ea = debug["raw_data"]["elo_away"]
    elo_diff = eh - ea + (0 if neutral else 100)
    exp_home = 1.0 / (1.0 + 10**(-elo_diff/400))
    steps["elo"] = {
        "formula": "P(home) = 1 / (1 + 10^(-diff/400))",
        "elo_home": eh, "elo_away": ea, "home_bonus": 0 if neutral else 100,
        "elo_diff": elo_diff,
        "expected_win": round(exp_home, 3),
        "interpretation": f"ELO 差 {elo_diff:.0f} 分, 主队预期胜率 {exp_home:.1%}",
    }

    # Massey
    mh = debug["raw_data"]["massey_home"]
    ma = debug["raw_data"]["massey_away"]
    diff_m = mh - ma + (0 if neutral else 0.35)
    steps["massey"] = {
        "formula": "P(home) = sigmoid(diff * 2.5)",
        "massey_home": mh, "massey_away": ma, "diff": round(diff_m, 3),
        "interpretation": f"Massey 分差 {diff_m:.2f}",
    }

    # Form
    fh = debug["raw_data"]["form_home"]
    fa = debug["raw_data"]["form_away"]
    steps["form"] = {
        "home_form": fh, "away_form": fa,
        "form_diff": round(fh["form_score"] - fa["form_score"], 3),
        "interpretation": f"主队状态分 {fh['form_score']:.2f} vs 客队 {fa['form_score']:.2f}",
    }

    # H2H
    h2h = debug["raw_data"]["h2h"]
    steps["head_to_head"] = {
        "total_matches": h2h.get("total_matches", 0),
        "record": f"{h2h.get('a_wins',0)}胜{h2h.get('draws',0)}平{h2h.get('b_wins',0)}负",
    }

    # Market odds (prior)
    steps["market_odds"] = {
        "note": "未输入赔率时使用先验 45/28/27",
    }

    # Monte Carlo
    steps["monte_carlo"] = {
        "simulations": 10000,
        "note": "基于前11个模型的概率分布进行10000次随机模拟取平均",
    }

    # Bayesian
    bayes_data = debug["raw_data"]
    steps["bayesian"] = {
        "note": "基于后验分布的5000次采样, 每队lambda裁剪到 0.05~5.0",
        "home_prior": f"N({bayes_model.prior_mean},{bayes_model.prior_std})",
    }

    debug["calculation_steps"] = steps

    # ---- 模型输出 ----
    predictions = {}
    predictions["poisson"] = poisson_model.predict(home_team, away_team, neutral)
    predictions["dixon_coles"] = dixon_coles_model.predict(home_team, away_team, neutral)
    predictions["elo"] = elo_model.predict_match(home_team, away_team, neutral)
    predictions["massey"] = massey_model.predict(home_team, away_team, neutral)
    predictions["form"] = form_model.predict(home_team, away_team, neutral)
    predictions["head_to_head"] = h2h_model.predict(home_team, away_team, neutral)
    predictions["market_odds"] = market_model.predict()
    predictions["bayesian"] = bayes_model.predict(home_team, away_team, neutral)

        # 让球胜负预测
    handicap = poisson_model.predict_handicap(home_team, away_team, neutral)
    
    blend = bma.blend(predictions)

    debug["model_outputs"] = {k: {
        "home_win": round(v.get("home_win",0), 4),
        "draw": round(v.get("draw",0), 4),
        "away_win": round(v.get("away_win",0), 4),
        "expected_goals": v.get("expected_total_goals"),
    } for k, v in predictions.items()}

    debug["ensemble"] = blend
    debug["weights"] = bma.get_weights()

    # HTFT
    debug["htft"] = poisson_model.predict_htft(home_team, away_team, neutral)

    return jsonify(_convert_numpy(debug))

@app.route("/history")
def history():
    return render_template("history.html")


@app.route("/api/calibration")
def api_calibration():
    import os
    report_file = "data/processed/calibration_report.json"
    weights_file = "ensemble/weights.json"
    result = {}
    if os.path.exists(report_file):
        with open(report_file, "r", encoding="utf-8") as f:
            result = json.load(f)
    elif os.path.exists(weights_file):
        with open(weights_file, "r", encoding="utf-8") as f:
            result = json.load(f)
    if not result:
        return jsonify({"error": "please run calibrate.py"})
    return jsonify(result)

@app.route("/api/rankings")
def api_rankings():
    return jsonify(elo_model.get_league_rankings()[:20])

if __name__ == "__main__":
    print("\n"+"="*60)
    print("  Soccer Prediction System v2.1")
    print("  12 Algorithms | Real Data Only")
    print("="*60)
    app.run(debug=False, host="127.0.0.1", port=5000)
