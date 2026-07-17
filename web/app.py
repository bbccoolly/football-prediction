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
from data.history_db import load_history

app = Flask(__name__)

# ---- 竞彩投注引擎 ----
from betting.jczq_engine import JczqEngine
from betting.jczq_planner import JczqPlanner, format_plan
from betting.jczq_fetcher import fetch_all_odds, SAMPLE_MATCHES
betting_engine = JczqEngine(upset_factor=0.12, is_national=True)
betting_planner = JczqPlanner(engine=betting_engine, budget=100.0, unit_price=2.0)

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
        # using pre-imported _cal_load_history
        from data.history_db import load_history as _lh
        history = _lh()
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

    # ===== 训练 XGBoost（如果未训练） =====
    if history and len(history) >= 100:
        if not xgb_model.is_trained:
            try:
                print("[Init] 训练 XGBoost (时间分割)...")
                sorted_hist = sorted(history, key=lambda m: m.get("date", "2000-01-01"))
                split_idx = int(len(sorted_hist) * 0.6)
                base_hist = sorted_hist[:split_idx]
                ml_hist = sorted_hist[split_idx:]

                from models.elo import EloRating as _Elo
                from models.form import FormModel as _Form
                from models.head_to_head import HeadToHeadModel as _H2H
                tmp_elo = _Elo(); tmp_elo.batch_update(base_hist)
                tmp_form = _Form(); tmp_form.load_history(base_hist)
                tmp_h2h = _H2H(); tmp_h2h.load_history(base_hist)

                X_list, y_res, y_goals = [], [], []
                for m in ml_hist:
                    try:
                        h2h = tmp_h2h.get_h2h(m["home_team"], m["away_team"])
                        home_form = tmp_form.get_form_score(m["home_team"])
                        away_form = tmp_form.get_form_score(m["away_team"])
                        fb = feature_builder.build(
                            elo_home=tmp_elo.get_rating(m["home_team"]),
                            elo_away=tmp_elo.get_rating(m["away_team"]),
                            atk_home=1.0, atk_away=1.0,
                            def_home=1.0, def_away=1.0,
                            form_home=home_form, form_away=away_form,
                            h2h_stats=h2h, squad_home=1.0, squad_away=1.0,
                            home_adv=HOME_ADVANTAGE.get(m.get("league","default"), 0.35),
                            neutral=m.get("league") in ["世界杯","欧洲杯","美洲杯","欧国联"],
                        )
                        X_list.append(fb["vector"])
                        hg, ag = m.get("home_goals",0), m.get("away_goals",0)
                        y_res.append(0 if hg>ag else (1 if hg==ag else 2))
                        y_goals.append(hg+ag)
                    except: continue

                if len(X_list) >= 50:
                    import numpy as np
                    xgb_model.fit(np.array(X_list), np.array(y_res), np.array(y_goals))
                    xgb_model.save()
                    print(f"[Init] XGBoost OK ({len(X_list)} samples)")
            except Exception as e:
                print(f"[Init] XGBoost train failed: {e}")

    _initialized = True
    print(f"[Init] {len(history)} matches, {len(_teams_cache)} teams, {len(_upcoming_matches_cache)} upcoming")
@app.route("/")
def index():
    _init_models()
    # Pre-load WC matches for server-side rendering
    from data.history_db import load_history
    from datetime import datetime, timedelta, timezone
    history = load_history()
    wc_matches = [m for m in history if m.get("league") == "世界杯" and m.get("home_goals") is not None]
    wc_matches = [m for m in wc_matches if m.get("home_team") != "?" and m.get("away_team") != "?"]
    wc_matches = [m for m in wc_matches if m.get("date","").startswith("2026")]
    def _bj_key(m):
        dt_str = m.get("date_time", m.get("date",""))
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z","+00:00"))
            return dt.astimezone(timezone(timedelta(hours=8))).isoformat()
        except:
            return dt_str
    wc_matches.sort(key=_bj_key, reverse=True)

    return render_template("index.html",
                          national_teams=NATIONAL_TEAMS,
                          club_teams=CLUB_TEAMS,
                          all_teams=_teams_cache,
                          players=SAMPLE_PLAYERS,
                          leagues=list(LEAGUES.keys()),
                          upcoming=_upcoming_matches_cache[:50],
                          wc_matches=wc_matches[:30],
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
    try:
        predictions["neural_net"] = nn_model.predict(fb["vector"])
    except:
        predictions["neural_net"] = {"model":"neural_net","home_win":0.35,"draw":0.30,"away_win":0.35,"status":"error","data_quality":0.1,"data_valid":False,"missing_teams":[],"using_defaults":True}
    report(10,"神经网络")

    preds_mc = [v for v in predictions.values()]
    w_mc = [bma.get_weights().get(k,0.08) for k in predictions.keys()]
    predictions["monte_carlo"] = mc_model.simulate(preds_mc, w_mc); report(11,"蒙特卡洛")
    predictions["bayesian"] = bayes_model.predict(home_team, away_team, neutral); report(12,"贝叶斯层次")

    handicap_result = poisson_model.predict_handicap(home_team, away_team, neutral)
    blend_result = bma.blend(predictions)

        # Only use valid models for confidence calculation
    valid_preds = {k: v for k, v in predictions.items() if v.get("data_valid", True)}
    if not valid_preds:
        valid_preds = predictions
    home_probs = [p["home_win"] for p in valid_preds.values()]
    draw_probs = [p["draw"] for p in valid_preds.values()]
    away_probs = [p["away_win"] for p in valid_preds.values()]
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
    home_odds = data.get("home_odds")
    draw_odds = data.get("draw_odds")
    away_odds = data.get("away_odds")

    if not home_team or not away_team:
        return jsonify({"error": "need teams"}), 400

    debug = {"home_team": home_team, "away_team": away_team, "neutral": neutral}

    # ---- 原始数据 ----
    elo_raw_home = elo_model.get_rating(home_team)
    elo_raw_away = elo_model.get_rating(away_team)
    home_bonus = 0 if neutral else 100
    debug["raw_data"] = {
        "elo_home": elo_raw_home,
        "elo_away": elo_raw_away,
        "elo_home_effective": elo_raw_home + home_bonus,  # 含主场加成
        "elo_away_effective": elo_raw_away,
        "home_bonus": home_bonus,
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
    raw_diff = eh - ea  # 原始分差（不含主场加成）
    elo_diff = raw_diff + home_bonus  # 有效分差（含主场加成）
    exp_home = 1.0 / (1.0 + 10**(-elo_diff/400))
    if home_bonus > 0 and raw_diff == 0:
        interp = f"两队ELO相同({eh:.0f})，主场加成 +{home_bonus} 分 → 有效分差 {elo_diff:.0f}，主队预期胜率 {exp_home:.1%}"
    elif home_bonus > 0:
        interp = f"原始分差 {raw_diff:.0f} + 主场加成 +{home_bonus} → 有效分差 {elo_diff:.0f}，主队预期胜率 {exp_home:.1%}"
    else:
        interp = f"中立场地无主场加成，ELO 差 {elo_diff:.0f} 分，主队预期胜率 {exp_home:.1%}"
    steps["elo"] = {
        "formula": "P(home) = 1 / (1 + 10^(-diff/400))",
        "elo_home": eh, "elo_away": ea,
        "elo_home_effective": eh + home_bonus, "elo_away_effective": ea,
        "home_bonus": home_bonus,
        "raw_diff": raw_diff, "elo_diff": elo_diff,
        "expected_win": round(exp_home, 3),
        "interpretation": interp,
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
    if home_odds and draw_odds and away_odds:
        try:
            ho = float(home_odds); do = float(draw_odds); ao = float(away_odds)
            total = 1/ho + 1/do + 1/ao
            steps["market_odds"] = {
                "formula": "P = (1/odds) / (1/H + 1/D + 1/A), 即去水头归一化",
                "odds_input": f"主{ho} / 平{do} / 客{ao}",
                "raw_probs": f"主{1/ho:.4f} / 平{1/do:.4f} / 客{1/ao:.4f}",
                "water_rate": f"{total:.4f}（水头 {(total-1)*100:.1f}%）",
                "interpretation": f"赔率反推：市场认为主胜概率约 {1/ho/total*100:.1f}%",
            }
        except:
            steps["market_odds"] = {"note": "未输入赔率时使用先验 45/28/27"}
    else:
        steps["market_odds"] = {"note": "未输入赔率时使用先验 45/28/27"}

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
    if home_odds and draw_odds and away_odds:
        try:
            predictions["market_odds"] = market_model.predict(home_odds=float(home_odds), draw_odds=float(draw_odds), away_odds=float(away_odds))
        except:
            predictions["market_odds"] = market_model.predict()
    else:
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

@app.route("/api/history/matches")
def api_history_matches():
    """历史比赛列表（分页）"""
    # using pre-imported _cal_load_history
    page = request.args.get("page", 1, type=int)
    league = request.args.get("league", "")
    team = request.args.get("team", "")
    per_page = 40
    
    history = load_history()
    if league:
        history = [m for m in history if m.get("league","") == league]
    if team:
        history = [m for m in history if team in m.get("home_team","") or team in m.get("away_team","")]
    
    history.sort(key=lambda m: m.get("date",""), reverse=True)
    total = len(history)
    start = (page - 1) * per_page
    matches = history[start:start+per_page]
    
    leagues = sorted(set(m.get("league","") for m in load_history() if m.get("league")))
    
    return jsonify({"matches": matches, "total": total, "page": page, "per_page": per_page, "leagues": leagues})

@app.route("/api/history/h2h")
def api_history_h2h():
    """两队交锋记录"""
    # using pre-imported _cal_load_history
    team_a = request.args.get("a", "")
    team_b = request.args.get("b", "")
    history = load_history()
    h2h = [m for m in history if (m.get("home_team")==team_a and m.get("away_team")==team_b) or (m.get("home_team")==team_b and m.get("away_team")==team_a)]
    h2h.sort(key=lambda m: m.get("date",""), reverse=True)
    return jsonify({"matches": h2h, "count": len(h2h)})

@app.route("/api/history/trend")
def api_history_trend():
    """球队近期趋势"""
    # using pre-imported _cal_load_history
    team = request.args.get("team", "")
    n = request.args.get("n", 20, type=int)
    history = load_history()
    matches = [m for m in history if m.get("home_team")==team or m.get("away_team")==team]
    matches.sort(key=lambda m: m.get("date",""), reverse=True)
    matches = matches[:n]
    
    trend = []
    for m in reversed(matches):
        is_home = m.get("home_team") == team
        gf = m.get("home_goals",0) if is_home else m.get("away_goals",0)
        ga = m.get("away_goals",0) if is_home else m.get("home_goals",0)
        if gf > ga: result = "W"
        elif gf == ga: result = "D"
        else: result = "L"
        trend.append({"date": m.get("date",""), "opponent": m.get("away_team","") if is_home else m.get("home_team",""), "gf": gf, "ga": ga, "result": result, "league": m.get("league","")})
    
    return jsonify({"team": team, "trend": trend, "count": len(trend)})

@app.route("/history")
def history():
    return render_template("history.html")

# ============================================================
# 竞彩投注分析
# ============================================================

@app.route("/betting")
def betting():
    return render_template("bet_plan.html")

@app.route("/api/betting/analyze", methods=["POST"])
def api_betting_analyze():
    """运行完整投注分析"""
    try:
        data = request.get_json() or {}
        upset = float(data.get("upset", 0.12))
        budget = float(data.get("budget", 100.0))
        use_sample = data.get("use_sample", False)
        
        if use_sample:
            matches = SAMPLE_MATCHES
        else:
            try:
                fetched = fetch_all_odds(force_refresh=True)
                matches = fetched.get("matches", [])
                if not matches:
                    matches = SAMPLE_MATCHES
            except Exception as e:
                matches = SAMPLE_MATCHES
        
        # 重建引擎(可调冷门因子)
        engine = JczqEngine(upset_factor=upset, is_national=True)
        planner = JczqPlanner(engine=engine, budget=budget, unit_price=2.0)
        
        plan = planner.generate_plan(matches)
        text_output = format_plan(plan)
        
        # 准备JSON响应
        bets_json = []
        for b in plan["bets"]:
            bets_json.append({
                "id": b["match_id"],
                "play": b["play_type"],
                "sel": b["selection"],
                "desc": b["desc"],
                "odds": b["odds"],
                "prob": round(b["algo_prob"] * 100, 1),
                "ev_pct": b["ev_pct"],
                "stake": b["stake"],
                "zhu": b["zhu"],
                "ret": b["expected_return"],
                "is_single": not b["parlay_only"],
            })
        
        return jsonify({
            "ok": True,
            "match_count": len(matches),
            "candidates": plan["candidates_count"],
            "bet_count": len(plan["bets"]),
            "single_count": len(plan["single_bets"]),
            "parlay_count": len(plan["parlay_bets"]),
            "total_stake": plan["total_stake"],
            "total_return": plan["total_expected_return"],
            "bets": bets_json,
            "text": text_output,
        })
    except Exception as e:
        import traceback
        return jsonify({"ok": False, "error": str(e), "trace": traceback.format_exc()}), 500

@app.route("/api/betting/matches")
def api_betting_matches():
    """获取比赛列表（预加载）"""
    try:
        fetched = fetch_all_odds(force_refresh=False)
        matches = fetched.get("matches", [])
        if not matches:
            matches = SAMPLE_MATCHES
        return jsonify({"ok": True, "count": len(matches), "matches": matches})
    except Exception as e:
        return jsonify({"ok": True, "count": len(SAMPLE_MATCHES), "matches": SAMPLE_MATCHES, "fallback": True})

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
    import json, os
    meta_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "processed", "team_meta.json")
    national_set = set()
    if os.path.exists(meta_path):
        with open(meta_path, "r", encoding="utf-8") as f:
            meta = json.load(f)
        national_set = set(meta.keys())
    from config import NATIONAL_TEAMS
    national_set.update(NATIONAL_TEAMS)
    all_rankings = elo_model.get_league_rankings()
    national = []
    clubs = []
    for team, rating in all_rankings:
        entry = {"team": team, "elo": round(rating, 0)}
        if team in national_set:
            conf = ""
            if os.path.exists(meta_path):
                with open(meta_path, "r", encoding="utf-8") as f:
                    meta2 = json.load(f)
                conf = meta2.get(team, {}).get("confederation", "")
            entry["confederation"] = conf
            national.append(entry)
        else:
            clubs.append(entry)
    return jsonify({
        "national": national[:50],
        "clubs": clubs[:30],
        "total_teams": len(all_rankings),
        "national_count": len(national),
        "club_count": len(clubs),
    })

@app.route("/api/wc_matches")
def api_wc_matches():
    """获取2026世界杯已完成比赛"""
    from data.history_db import load_history
    from datetime import datetime, timedelta, timezone
    history = load_history()
    wc = [m for m in history if m.get("league") == "世界杯" and m.get("home_goals") is not None]
    wc = [m for m in wc if m.get("home_team") != "?" and m.get("away_team") != "?"]
    wc = [m for m in wc if m.get("date","").startswith("2026")]
    # Sort by Beijing time (UTC+8)
    def bj_key(m):
        dt_str = m.get("date_time", m.get("date",""))
        try:
            dt = datetime.fromisoformat(dt_str.replace("Z","+00:00"))
            bj_dt = dt.astimezone(timezone(timedelta(hours=8)))
            return bj_dt.isoformat()
        except:
            return dt_str
    wc.sort(key=bj_key, reverse=True)
    return jsonify({"count": len(wc), "matches": wc})

@app.route("/api/sync_fifa")
def api_sync_fifa():
    """同步FIFA世界杯比赛数据"""
    from data.history_db import load_history, add_match
    from models.elo import EloRating
    from models.poisson import build_strengths_from_results
    import requests as req, unicodedata
    from datetime import datetime, timedelta, timezone

    try:
        H = {"User-Agent": "Mozilla/5.0"}
        EN_CN = {
            "Argentina":"阿根廷","Brazil":"巴西","Germany":"德国","France":"法国","Spain":"西班牙",
            "England":"英格兰","Italy":"意大利","Netherlands":"荷兰","Portugal":"葡萄牙","Belgium":"比利时",
            "Croatia":"克罗地亚","Uruguay":"乌拉圭","Colombia":"哥伦比亚","Mexico":"墨西哥",
            "Japan":"日本","Korea Republic":"韩国","Iran":"伊朗","IR Iran":"伊朗",
            "Saudi Arabia":"沙特阿拉伯","Australia":"澳大利亚","Senegal":"塞内加尔",
            "Morocco":"摩洛哥","Tunisia":"突尼斯","Ghana":"加纳","Cameroon":"喀麦隆",
            "Nigeria":"尼日利亚","Egypt":"埃及","Algeria":"阿尔及利亚",
            "Costa Rica":"哥斯达黎加","USA":"美国","Canada":"加拿大","Panama":"巴拿马",
            "Ecuador":"厄瓜多尔","Peru":"秘鲁","Chile":"智利","Paraguay":"巴拉圭",
            "Switzerland":"瑞士","Austria":"奥地利","Serbia":"塞尔维亚","Denmark":"丹麦",
            "Sweden":"瑞典","Norway":"挪威","Poland":"波兰","Czechia":"捷克",
            "Ukraine":"乌克兰","Turkey":"土耳其","Greece":"希腊","Scotland":"苏格兰",
            "Wales":"威尔士","Hungary":"匈牙利","Slovakia":"斯洛伐克","Romania":"罗马尼亚",
            "Finland":"芬兰","Iceland":"冰岛","Slovenia":"斯洛文尼亚",
            "Bosnia and Herzegovina":"波黑","Georgia":"格鲁吉亚","Israel":"以色列",
            "Venezuela":"委内瑞拉","Haiti":"海地","South Africa":"南非",
            "Qatar":"卡塔尔","Iraq":"伊拉克","United Arab Emirates":"阿联酋",
            "New Zealand":"新西兰","Burkina Faso":"布基纳法索","Mali":"马里",
            "Cote d'Ivoire":"科特迪瓦","Côte d'Ivoire":"科特迪瓦",
            "Türkiye":"土耳其","Curaçao":"库拉索","Curacao":"库拉索",
            "Cabo Verde":"佛得角","Congo DR":"刚果民主共和国","Jordan":"约旦",
        }
        def tr(n):
            if not n: return "?"
            if n in EN_CN: return EN_CN[n]
            n_norm = unicodedata.normalize('NFKD', n).encode('ascii','ignore').decode()
            for e, c in EN_CN.items():
                e_norm = unicodedata.normalize('NFKD', e).encode('ascii','ignore').decode()
                if e_norm.lower() == n_norm.lower(): return c
                if e_norm.lower() in n_norm.lower() or n_norm.lower() in e_norm.lower(): return c
            return n

        history = load_history()
        db_keys = set((m.get("home_team",""), m.get("away_team",""), m.get("date","")) for m in history)
        added = 0
        fetched = 0
        
        today = datetime.now(timezone.utc)
        # Query in 3-day chunks to avoid API pagination limits
        for days_back in range(14, -1, -3):
            sd = (today - timedelta(days=min(days_back+2, 14))).strftime("%Y-%m-%dT00:00:00Z")
            ed = (today - timedelta(days=max(days_back-1, 0))).strftime("%Y-%m-%dT23:59:59Z")
            try:
                r = req.get("https://api.fifa.com/api/v3/calendar/matches",
                    params={"language":"en","count":200,"from":sd,"to":ed}, headers=H, timeout=15)
                results = r.json().get("Results",[])
                for m in results:
                    comp = (m.get("CompetitionName",[{}]) or [{}])[0].get("Description","")
                    if "World Cup" not in comp or "Women" in comp: continue
                    fetched += 1
                    
                    home = m.get("Home",{})
                    away = m.get("Away",{})
                    hn = tr((home.get("TeamName",[{}]) or [{}])[0].get("Description") or home.get("ShortClubName") or "")
                    an = tr((away.get("TeamName",[{}]) or [{}])[0].get("Description") or away.get("ShortClubName") or "")
                    hs = m.get("HomeTeamScore")
                    aws = m.get("AwayTeamScore")
                    if hs is None or aws is None: continue
                    if hn == "?" or an == "?" or hn == an: continue
                    
                    key = (hn, an, (m.get("Date") or "")[:10])
                    if key not in db_keys:
                        match = {"home_team":hn,"away_team":an,"home_goals":int(hs),"away_goals":int(aws),"league":"世界杯","date":key[2]}
                        add_match(match)
                        db_keys.add(key)
                        added += 1
            except: pass

        if added > 0:
            h2 = load_history()
            elo = EloRating(); elo.load(); elo.batch_update(h2); elo.save()
            strengths = build_strengths_from_results(h2)
            poisson_model.set_team_strengths(strengths)

        return jsonify({"status":"ok","fetched":fetched,"added":added,"total_history":len(load_history())})
    except Exception as e:
        return jsonify({"error":str(e)}),500

# Pre-import calibrate to avoid thread issues
try:
    from calibrate import backtest as _cal_backtest, calibrate_weights as _cal_weights
    # using pre-imported _cal_load_history as _cal_load_history
    CAL_AVAILABLE = True
except Exception as e:
    print(f"[Calibrate] Import warning: {e}")
    CAL_AVAILABLE = False

# ========== 校准系统 Web 界面 ==========
_calibration_state = {"running": False, "progress": 0, "message": "", "report": None, "weights": None, "total": 0}

@app.route("/rankings")
def rankings_page():
    from flask import redirect
    return redirect("/history#rankings")

@app.route("/calibrate")
def calibrate_page():
    return render_template("calibrate.html")

@app.route("/api/calibrate/run")
def api_calibrate_run():
    """启动校准（后台运行 calibrate.py）"""
    import json, os, subprocess, sys
    
    # Check if already running via status file
    status_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "processed", "calibration_status.json")
    if os.path.exists(status_file):
        with open(status_file, "r", encoding="utf-8") as f:
            s = json.load(f)
        if not s.get("done", True):
            return jsonify({"error": "校准已在运行中", "progress": s.get("progress",0), "message": s.get("message","")}), 409
    
    # Start calibrate.py in background
    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "calibrate.py")
    try:
        subprocess.Popen([sys.executable, script], cwd=os.path.dirname(script),
                        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        return jsonify({"status": "started", "message": "calibrate.py 已在后台启动，约需 1-3 分钟"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/lottery/predict/<lottery_key>")
@app.route("/api/lottery/predict/<lottery_key>")
def api_lottery_predict(lottery_key):
    """prediction API: ?force=1 refresh data, ?regen=1 regenerate only"""
    from lottery_fetcher import fetch_lottery
    from lottery_predictor import full_analysis, generate_prediction
    force = request.args.get("force", "0") == "1"
    regen = request.args.get("regen", "0") == "1"
    try:
        history = fetch_lottery(lottery_key, count=50, force_refresh=force)
        if not history:
            return jsonify({"error": f"no data for {lottery_key}"}), 404

        if regen:
            analysis = full_analysis(lottery_key, history)
            return jsonify(analysis)

        analysis = full_analysis(lottery_key, history)
        return jsonify(analysis)

    except Exception as e:
        return jsonify({"error": str(e)}), 500
@app.route("/api/calibrate/status")
def api_calibrate_status():
    """查询校准状态（读取 calibrate.py 写入的状态文件）"""
    import json, os
    status_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "processed", "calibration_status.json")
    if os.path.exists(status_file):
        with open(status_file, "r", encoding="utf-8") as f:
            s = json.load(f)
        # If done, also load the report
        if s.get("done"):
            report_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "processed", "calibration_report.json")
            if os.path.exists(report_file):
                with open(report_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                s["report"] = data.get("report", {})
                s["weights"] = data.get("weights", {})
                s["total"] = data.get("total_matches", 0)
        return jsonify(s)
    return jsonify({"progress": 0, "message": "尚未运行校准", "done": True})

@app.route("/api/calibrate/report")
def api_calibrate_report():
    import json, os
    report_file = "data/processed/calibration_report.json"
    if os.path.exists(report_file):
        with open(report_file, "r", encoding="utf-8") as f:
            return jsonify(json.load(f))
    return jsonify({"error":"暂无校准报告"}), 404

# ========== 彩票开奖查询 ==========
from lottery_fetcher import fetch_all as _fetch_all_lottery

@app.route("/lottery")
def lottery_page():
    """彩票开奖结果页面"""
    return render_template("lottery.html")

@app.route("/api/lottery")
def api_lottery():
    """获取彩票开奖数据"""
    try:
        data = _fetch_all_lottery(count=30)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
if __name__ == "__main__":
    print("\n"+"="*60)
    print("  Soccer Prediction System v2.1")
    print("  12 Algorithms | Real Data Only")
    print("="*60)
    app.run(debug=False, host="127.0.0.1", port=5000)
