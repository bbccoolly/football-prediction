import sys, os, json, math, time, threading, hmac
from functools import wraps
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
from ensemble.prediction_contract import NoAvailableModelsError, normalize_prediction
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
_model_init_lock = threading.RLock()

def _init_models():
    global _initialized
    if _initialized:
        return
    with _model_init_lock:
        if _initialized:
            return
        _initialize_models_unlocked()

def _initialize_models_unlocked():
    global _upcoming_matches_cache, _fetch_errors, _teams_cache, _initialized

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
        history_fingerprint = elo_model.fingerprint_matches(history)
        if not elo_model.load(history_fingerprint):
            print("[Init] 重建 ELO（数据或参数已变化）...")
            elo_model.rebuild(history)
            elo_model.save()
        strengths = build_strengths_from_results(history)
        poisson_model.set_team_strengths(strengths)
        dixon_coles_model.set_team_strengths(strengths)
        massey_model.fit(history)
        form_model.load_history(history)
        h2h_model.load_history(history)
        for m in history[:100]:
            fv = knn_model.feature_vector(1.0,1.0,1.0,1.0,
                form_model.get_form_score(m["home_team"])["form_score"],
                form_model.get_form_score(m["away_team"])["form_score"],
                elo_model.get_rating(m["home_team"]), elo_model.get_rating(m["away_team"]),
                elo_model.get_rating(m["home_team"])-elo_model.get_rating(m["away_team"]))
            knn_model.add_match(fv, m.get("home_goals",0), m.get("away_goals",0))
        bayes_model.fit(history)
    elif not elo_model.load():
        print("[Init] 无历史数据和有效 ELO 产物，使用默认评分")

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
                    except (KeyError, TypeError, ValueError) as exc:
                        print(f"[Init] 跳过无效训练样本: {exc}")
                        continue

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
        except (TypeError, ValueError):
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


class PredictionInputError(ValueError):
    def __init__(self, message, code):
        super().__init__(message)
        self.code = code


def _api_error(message, code, status, details=None):
    return jsonify({
        "error": message,
        "error_code": code,
        "details": details or [],
    }), status


def _admin_required(operation):
    @wraps(operation)
    def wrapped(*args, **kwargs):
        expected_token = os.environ.get("FOOTBALL_ADMIN_TOKEN")
        if not expected_token:
            return _api_error(
                "管理令牌尚未配置",
                "ADMIN_TOKEN_NOT_CONFIGURED",
                503,
            )
        authorization = request.headers.get("Authorization", "")
        scheme, separator, supplied_token = authorization.partition(" ")
        if (
            not separator
            or scheme.lower() != "bearer"
            or not hmac.compare_digest(supplied_token, expected_token)
        ):
            return _api_error(
                "需要有效的管理令牌",
                "ADMIN_AUTH_REQUIRED",
                401,
            )
        return operation(*args, **kwargs)

    return wrapped


def _parse_prediction_input(data, default_neutral=True):
    if not isinstance(data, dict):
        raise PredictionInputError("请求体必须是 JSON 对象", "INVALID_JSON")

    home_team = str(data.get("home_team") or "").strip()
    away_team = str(data.get("away_team") or "").strip()
    if not home_team or not away_team:
        raise PredictionInputError("请选择主队和客队", "MISSING_TEAMS")
    if home_team == away_team:
        raise PredictionInputError("主客队不能相同", "SAME_TEAM")

    neutral = data.get("neutral", default_neutral)
    if not isinstance(neutral, bool):
        raise PredictionInputError("neutral 必须是布尔值", "INVALID_NEUTRAL")

    odds_keys = ("home_odds", "draw_odds", "away_odds")
    supplied = [data.get(key) not in (None, "") for key in odds_keys]
    odds = (None, None, None)
    if any(supplied):
        if not all(supplied):
            raise PredictionInputError("赔率必须同时提供主胜、平局和客胜", "INVALID_ODDS")
        try:
            odds = tuple(float(data[key]) for key in odds_keys)
        except (TypeError, ValueError):
            raise PredictionInputError("赔率必须是有效数字", "INVALID_ODDS") from None
        if any(not math.isfinite(value) or value <= 1.0 for value in odds):
            raise PredictionInputError("赔率必须是大于 1 的有限数字", "INVALID_ODDS")

    home_missing = data.get("home_missing", [])
    away_missing = data.get("away_missing", [])
    if not isinstance(home_missing, list) or not isinstance(away_missing, list):
        raise PredictionInputError("缺阵球员必须使用数组", "INVALID_MISSING_PLAYERS")

    league_cn = str(data.get("league") or "世界杯")
    return {
        "home_team": home_team,
        "away_team": away_team,
        "league_cn": league_cn,
        "league": LEAGUES.get(league_cn, "world_cup"),
        "neutral": neutral,
        "home_missing": home_missing,
        "away_missing": away_missing,
        "home_odds": odds[0],
        "draw_odds": odds[1],
        "away_odds": odds[2],
        "task_id": str(data.get("task_id") or "default"),
    }


def _safe_prediction(model_id, operation):
    try:
        raw_result = operation()
    except Exception as exc:
        print(f"[Predict] {model_id} failed: {exc}")
        raw_result = {
            "model": model_id,
            "status": "error",
            "warnings": ["prediction_failed"],
        }
    return normalize_prediction(model_id, raw_result)


def _model_agreement(predictions):
    valid = [prediction for prediction in predictions.values() if prediction.get("available")]
    if len(valid) < 2:
        return 0.0

    deviations = []
    for field in ("home_win", "draw", "away_win"):
        values = [prediction[field] for prediction in valid]
        average = sum(values) / len(values)
        deviations.append(math.sqrt(sum((value - average) ** 2 for value in values) / len(values)))
    return max(0.0, min(100.0, round(100 * (1.0 - sum(deviations) / 3 * 5), 1)))


def _run_predictions(context, report_progress=None):
    report_progress = report_progress or (lambda _done, _name: None)
    home_team = context["home_team"]
    away_team = context["away_team"]
    neutral = context["neutral"]

    request_player_impact = PlayerImpact()
    for team, missing in (
        (home_team, context["home_missing"]),
        (away_team, context["away_missing"]),
    ):
        if team in SAMPLE_PLAYERS:
            request_player_impact.set_squad(team, SAMPLE_PLAYERS[team])
        request_player_impact.set_injuries(team, missing)
    squad_info = request_player_impact.both_teams_impact(home_team, away_team)
    home_adv = HOME_ADVANTAGE.get(
        context["league_cn"], HOME_ADVANTAGE.get(context["league"], 0.35)
    )

    predictions = {}
    steps = [
        ("poisson", "泊松分布", lambda: poisson_model.predict(home_team, away_team, neutral)),
        ("dixon_coles", "Dixon-Coles", lambda: dixon_coles_model.predict(home_team, away_team, neutral)),
        ("elo", "ELO评级", lambda: elo_model.predict_match(home_team, away_team, neutral)),
        ("massey", "Massey排名", lambda: massey_model.predict(home_team, away_team, neutral)),
        ("form", "近期状态", lambda: form_model.predict(home_team, away_team, neutral)),
        ("head_to_head", "交锋记录", lambda: h2h_model.predict(home_team, away_team, neutral)),
    ]
    for index, (model_id, display_name, operation) in enumerate(steps, start=1):
        predictions[model_id] = _safe_prediction(model_id, operation)
        report_progress(index, display_name)

    def market_prediction():
        if context["home_odds"] is not None:
            return market_model.predict(
                home_odds=context["home_odds"],
                draw_odds=context["draw_odds"],
                away_odds=context["away_odds"],
            )
        return market_model.predict()

    predictions["market_odds"] = _safe_prediction("market_odds", market_prediction)
    report_progress(7, "市场赔率")

    knn_features = knn_model.feature_vector(
        poisson_model.attack_strengths.get(home_team, 1.0),
        poisson_model.defense_strengths.get(home_team, 1.0),
        poisson_model.attack_strengths.get(away_team, 1.0),
        poisson_model.defense_strengths.get(away_team, 1.0),
        form_model.get_form_score(home_team)["form_score"],
        form_model.get_form_score(away_team)["form_score"],
        elo_model.get_rating(home_team), elo_model.get_rating(away_team),
        elo_model.get_rating(home_team) - elo_model.get_rating(away_team),
    )
    predictions["knn_similar"] = _safe_prediction(
        "knn_similar", lambda: knn_model.predict(knn_features)
    )
    report_progress(8, "KNN相似")

    feature_data = feature_builder.build(
        elo_home=elo_model.get_rating(home_team), elo_away=elo_model.get_rating(away_team),
        atk_home=poisson_model.attack_strengths.get(home_team, 1.0),
        atk_away=poisson_model.attack_strengths.get(away_team, 1.0),
        def_home=poisson_model.defense_strengths.get(home_team, 1.0),
        def_away=poisson_model.defense_strengths.get(away_team, 1.0),
        form_home=form_model.get_form_score(home_team),
        form_away=form_model.get_form_score(away_team),
        h2h_stats=h2h_model.get_h2h(home_team, away_team),
        squad_home=squad_info["home_completeness"],
        squad_away=squad_info["away_completeness"],
        home_adv=home_adv, neutral=neutral,
    )
    predictions["xgboost"] = _safe_prediction(
        "xgboost", lambda: xgb_model.predict(feature_data["vector"])
    )
    report_progress(9, "XGBoost")
    predictions["neural_net"] = _safe_prediction(
        "neural_net", lambda: nn_model.predict(feature_data["vector"])
    )
    report_progress(10, "神经网络")

    predictions["bayesian"] = _safe_prediction(
        "bayesian", lambda: bayes_model.predict(home_team, away_team, neutral)
    )
    report_progress(11, "贝叶斯层次")

    ensemble_result = bma.blend(predictions)
    independent_predictions = dict(predictions)
    simulation = mc_model.simulate([ensemble_result], [1.0])
    simulation["role"] = "derived"
    simulation["source"] = "ensemble"
    predictions["monte_carlo"] = {
        **simulation,
        "model_id": "monte_carlo",
        "available": False,
        "status": "derived",
        "warnings": ["derived_output"],
    }
    report_progress(12, "蒙特卡洛模拟")

    model_agreement = _model_agreement(independent_predictions)
    warnings = list(bma.load_warnings)
    for prediction in independent_predictions.values():
        warnings.extend(prediction.get("warnings", []))
    warnings = list(dict.fromkeys(warnings))
    if sum(1 for prediction in independent_predictions.values() if prediction.get("available")) < 2:
        warnings.append("insufficient_models_for_agreement")

    model_summary = {
        "total_models": len(independent_predictions),
        "available_models": sum(1 for prediction in independent_predictions.values() if prediction.get("available")),
        "excluded_models": sum(1 for prediction in independent_predictions.values() if not prediction.get("available")),
        "unknown_quality_models": sum(
            1 for prediction in independent_predictions.values()
            if prediction.get("available") and prediction.get("data_quality") is None
        ),
        "using_defaults_models": sum(
            1 for prediction in independent_predictions.values() if prediction.get("using_defaults")
        ),
    }
    return {
        "predictions": predictions,
        "ensemble": ensemble_result,
        "simulation": simulation,
        "model_agreement": model_agreement,
        "model_summary": model_summary,
        "warnings": warnings,
        "squad_info": squad_info,
        "htft": poisson_model.predict_htft(home_team, away_team, neutral),
        "handicap": poisson_model.predict_handicap(home_team, away_team, neutral),
    }

@app.route("/predict", methods=["POST"])
def predict():
    try:
        context = _parse_prediction_input(request.get_json(silent=True), default_neutral=True)
    except PredictionInputError as exc:
        return _api_error(str(exc), exc.code, 400)
    _init_models()

    task_id = context["task_id"]
    _prediction_progress[task_id] = {"total": 12, "done": 0, "current": "准备中..."}
    def report(n,name):
        if task_id in _prediction_progress:
            _prediction_progress[task_id]["done"]=n
            _prediction_progress[task_id]["current"]=name
    try:
        result = _run_predictions(context, report)
    except NoAvailableModelsError as exc:
        return _api_error(str(exc), "NO_AVAILABLE_MODELS", 503)
    except Exception as exc:
        print(f"[Predict] unexpected failure: {exc}")
        return _api_error("预测服务内部错误", "INTERNAL_ERROR", 500)

    return jsonify(_convert_numpy({
        "home_team": context["home_team"], "away_team": context["away_team"],
        "neutral": context["neutral"], "league": context["league"],
        "squad_info": result["squad_info"],
        "predictions": result["predictions"],
        "htft": result["htft"],
        "ensemble": result["ensemble"],
        "simulation": result.get("simulation"),
        "handicap": result["handicap"],
        "model_agreement": result["model_agreement"],
        "confidence": result["model_agreement"],
        "model_summary": result["model_summary"],
        "warnings": result["warnings"],
    }))

@app.route("/api/upcoming")
def api_upcoming():
    return jsonify({"upcoming":_upcoming_matches_cache[:80],"count":len(_upcoming_matches_cache),"data_available":len(_upcoming_matches_cache)>0})

@app.route("/api/refresh_data", methods=["POST"])
@_admin_required
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
    try:
        context = _parse_prediction_input(request.get_json(silent=True), default_neutral=False)
    except PredictionInputError as exc:
        return _api_error(str(exc), exc.code, 400)
    _init_models()
    home_team = context["home_team"]
    away_team = context["away_team"]
    neutral = context["neutral"]
    home_odds = context["home_odds"]
    draw_odds = context["draw_odds"]
    away_odds = context["away_odds"]

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

    # Market odds
    if home_odds and draw_odds and away_odds:
        ho = home_odds; do = draw_odds; ao = away_odds
        total = 1/ho + 1/do + 1/ao
        steps["market_odds"] = {
            "formula": "P = (1/odds) / (1/H + 1/D + 1/A), 即去水头归一化",
            "odds_input": f"主{ho} / 平{do} / 客{ao}",
            "raw_probs": f"主{1/ho:.4f} / 平{1/do:.4f} / 客{1/ao:.4f}",
            "water_rate": f"{total:.4f}（水头 {(total-1)*100:.1f}%）",
            "interpretation": f"赔率反推：市场认为主胜概率约 {1/ho/total*100:.1f}%",
        }
    else:
        steps["market_odds"] = {"note": "未输入真实赔率，市场模型退出本次融合"}

    # KNN
    steps["knn_similar"] = {
        "k": 20,
        "note": "基于18维特征向量，找出历史上最相似的20场比赛，加权投票",
    }

    # XGBoost
    steps["xgboost"] = {
        "estimators": 200,
        "note": "200棵梯度提升树，双头输出（胜平负+总进球），18维特征输入",
    }

    # Neural Net
    steps["neural_net"] = {
        "architecture": "18→32→16→3",
        "note": "3层全连接网络，ReLU激活 + Softmax输出，SGD训练",
    }

    # Monte Carlo
    steps["monte_carlo"] = {
        "simulations": 10000,
        "note": "对最终融合概率进行派生采样，不作为独立模型重复参与融合",
    }

    # Bayesian
    bayes_data = debug["raw_data"]
    steps["bayesian"] = {
        "note": "基于后验分布的5000次采样, 每队lambda裁剪到 0.05~5.0",
        "home_prior": f"N({bayes_model.prior_mean},{bayes_model.prior_std})",
    }

    debug["calculation_steps"] = steps

    try:
        prediction_result = _run_predictions(context)
    except NoAvailableModelsError as exc:
        return _api_error(str(exc), "NO_AVAILABLE_MODELS", 503)
    predictions = prediction_result["predictions"]

    debug["model_outputs"] = {k: {
        "home_win": round(v["home_win"], 4) if v.get("home_win") is not None else None,
        "draw": round(v["draw"], 4) if v.get("draw") is not None else None,
        "away_win": round(v["away_win"], 4) if v.get("away_win") is not None else None,
        "expected_goals": v.get("expected_total_goals"),
        "available": v.get("available"),
        "status": v.get("status"),
    } for k, v in predictions.items()}

    debug["ensemble"] = prediction_result["ensemble"]
    debug["simulation"] = prediction_result.get("simulation")
    debug["weights"] = prediction_result["ensemble"]["effective_weights"]
    debug["model_agreement"] = prediction_result["model_agreement"]
    debug["model_summary"] = prediction_result["model_summary"]
    debug["warnings"] = prediction_result["warnings"]

    # HTFT
    debug["htft"] = prediction_result["htft"]
    debug["handicap"] = prediction_result["handicap"]

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
        except (TypeError, ValueError):
            return dt_str
    wc.sort(key=bj_key, reverse=True)
    return jsonify({"count": len(wc), "matches": wc})


def _refresh_models_after_history_change():
    """Compatibility refresh until PR 3 replaces globals with an atomic snapshot."""
    global elo_model, poisson_model
    from data.history_db import load_history
    from models.elo import EloRating
    from models.poisson import PoissonModel, build_strengths_from_results

    history = load_history()
    refreshed_elo = EloRating()
    refreshed_elo.rebuild(history)
    refreshed_elo.save()
    refreshed_poisson = PoissonModel()
    refreshed_poisson.set_team_strengths(build_strengths_from_results(history))
    with _model_init_lock:
        elo_model = refreshed_elo
        poisson_model = refreshed_poisson

@app.route("/api/sync_fifa", methods=["POST"])
@_admin_required
def api_sync_fifa():
    """同步FIFA世界杯比赛数据"""
    from data.fifa_sync import fetch_recent_fifa_source_records
    from data.history_db import load_history
    from data.match_repository import (
        RepositoryNotInitializedError,
        get_default_repository,
    )

    try:
        repository = get_default_repository()
        sync_run_id = repository.create_sync_run("fifa_recent", {"days": 14})
        fetched = fetch_recent_fifa_source_records(days=14)
        counts = repository.import_source_records(
            fetched["records"],
            sync_run_id,
            sync_type="fifa_recent",
        )

        changed = counts["inserted"] + counts["updated"]
        if changed > 0:
            _refresh_models_after_history_change()

        return jsonify({
            "status": "ok" if not fetched["errors"] else "partial",
            "sync_run_id": sync_run_id,
            **counts,
            "fetched": fetched["fetched"],
            "errors": fetched["errors"],
            "total_history": len(load_history()),
        })
    except RepositoryNotInitializedError:
        return _api_error(
            "比赛数据库尚未初始化，请先执行历史数据迁移",
            "MATCH_REPOSITORY_NOT_INITIALIZED",
            503,
        )
    except Exception as e:
        print(f"[FIFA] 同步失败: {e}")
        return _api_error("FIFA 数据同步失败", "FIFA_SYNC_FAILED", 500)

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

@app.route("/api/calibrate/run", methods=["POST"])
@_admin_required
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
def _lottery_prediction(lottery_key, force_refresh=False):
    from lottery_fetcher import fetch_lottery
    from lottery_predictor import full_analysis
    try:
        history = fetch_lottery(
            lottery_key,
            count=50,
            force_refresh=force_refresh,
        )
        if not history:
            return jsonify({"error": f"no data for {lottery_key}"}), 404
        analysis = full_analysis(lottery_key, history)
        return jsonify(analysis)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lottery/predict/<lottery_key>", methods=["GET"])
def api_lottery_predict(lottery_key):
    """使用现有数据生成彩票统计分析。"""
    if request.args.get("force") == "1" or request.args.get("regen") == "1":
        return _api_error(
            "强制刷新必须使用受保护的 POST 请求",
            "WRITE_REQUIRES_POST",
            405,
        )
    return _lottery_prediction(lottery_key)


@app.route("/api/lottery/predict/<lottery_key>", methods=["POST"])
@_admin_required
def api_lottery_predict_refresh(lottery_key):
    """刷新数据并重新生成彩票统计分析。"""
    payload = request.get_json(silent=True) or {}
    return _lottery_prediction(
        lottery_key,
        force_refresh=bool(payload.get("force_refresh", False)),
    )
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

@app.route("/api/lottery", methods=["GET"])
def api_lottery():
    """获取彩票开奖数据"""
    try:
        data = _fetch_all_lottery(count=30)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/lottery", methods=["POST"])
@_admin_required
def api_lottery_refresh():
    """强制刷新彩票开奖数据。"""
    try:
        data = _fetch_all_lottery(count=30, force_refresh=True)
        return jsonify(data)
    except Exception as e:
        return jsonify({"error": str(e)}), 500
if __name__ == "__main__":
    print("\n"+"="*60)
    print("  Soccer Prediction System v2.1")
    print("  11 Models | Derived Simulation | Real Data Only")
    print("="*60)
    app.run(debug=False, host="127.0.0.1", port=5000)
