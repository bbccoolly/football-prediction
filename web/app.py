import sys, os, json, math, time, threading, hmac
from datetime import datetime, timezone
from functools import wraps
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, request, jsonify, Response, stream_with_context
import numpy as np

from ensemble.prediction_contract import NoAvailableModelsError
from config import *
from data.history_db import load_history
from data.match_repository import (
    DEFAULT_DATABASE_PATH,
    MatchRepository,
    RepositoryNotInitializedError,
)
from prediction import (
    InvalidPredictionRequestError,
    ModelExecutionError,
    ModelRuntimeBuilder,
    PredictionService,
    RuntimeManager,
    RuntimeNotReadyError,
    RuntimeRefreshInProgressError,
    SnapshotTimeMismatchError,
)

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

_upcoming_matches_cache = []
_fetch_errors = []
_teams_cache = list(ALL_TEAMS)
_initialized = False
_model_init_lock = threading.RLock()
_runtime_database_path = None
_repository = None
_runtime_manager = None
_prediction_service = None


def _configured_database_path():
    return os.path.abspath(os.environ.get("FOOTBALL_DB_PATH", str(DEFAULT_DATABASE_PATH)))


def _ensure_runtime_components():
    global _runtime_database_path, _repository, _runtime_manager, _prediction_service
    database_path = _configured_database_path()
    if _repository is not None and _runtime_database_path == database_path:
        return
    _repository = MatchRepository(database_path)
    _runtime_manager = RuntimeManager(ModelRuntimeBuilder(_repository))
    _prediction_service = PredictionService(_repository, _runtime_manager)
    _runtime_database_path = database_path
    app.extensions["match_repository"] = _repository
    app.extensions["runtime_manager"] = _runtime_manager
    app.extensions["prediction_service"] = _prediction_service


def _normalize_upcoming(matches):
    _ensure_runtime_components()
    normalized = []
    for raw in matches:
        match = dict(raw)
        source = str(match.get("source") or "*")
        league_raw = str(match.get("league") or "")
        competition_value = match.get("competition_id") or league_raw
        try:
            home = _repository.resolve_team(
                match.get("home_team", ""), source, "unknown"
            ) or _repository.resolve_team_unique(match.get("home_team", ""))
            away = _repository.resolve_team(
                match.get("away_team", ""), source, "unknown"
            ) or _repository.resolve_team_unique(match.get("away_team", ""))
            competition = (
                _repository.resolve_competition(str(competition_value))
                if competition_value else None
            )
        except RepositoryNotInitializedError:
            # Refresh remains usable before the local repository is initialized,
            # but unresolved cache entries must stay explicitly unpredictable.
            home = away = competition = None
        predictable = bool(
            home and away and competition
            and home["team_id"] != away["team_id"]
            and home["team_type"] == away["team_type"]
        )
        if not home or not away:
            resolution_status = "unmatched"
        elif home["team_id"] == away["team_id"]:
            resolution_status = "same_team"
        elif home["team_type"] != away["team_type"]:
            resolution_status = "mixed_team_types"
        elif not competition:
            resolution_status = "unknown_competition"
        else:
            resolution_status = "resolved"
        match.update({
            "home_team_raw": match.get("home_team", ""),
            "away_team_raw": match.get("away_team", ""),
            "home_team_id": home["team_id"] if home else None,
            "away_team_id": away["team_id"] if away else None,
            "home_team": home["canonical_name"] if home else match.get("home_team", ""),
            "away_team": away["canonical_name"] if away else match.get("away_team", ""),
            "league_raw": league_raw,
            "competition_id": competition["competition_id"] if competition else None,
            "competition_name": competition["canonical_name"] if competition else league_raw,
            "predictable": predictable,
            "resolution_status": resolution_status,
        })
        normalized.append(match)
    return normalized

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
    _ensure_runtime_components()
    snapshot = _runtime_manager.initialize()
    from data.fetcher import load_cached
    cached = load_cached()
    _upcoming_matches_cache = _normalize_upcoming(cached.get("upcoming", []))
    _fetch_errors = list(cached.get("errors", []))
    _teams_cache = [team["canonical_name"] for team in _repository.list_teams()]
    _initialized = True
    print(
        f"[Init] runtime {snapshot.snapshot_id[:20]}, "
        f"{snapshot.training_sample_count} matches, {len(_teams_cache)} teams, "
        f"{len(_upcoming_matches_cache)} upcoming"
    )
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
    """返回数据和预测运行时状态。"""
    try:
        _init_models()
        runtime = _runtime_manager.status()
    except Exception as exc:
        runtime = {
            "ready": False,
            "runtime_stale": True,
            "last_refresh_error": {
                "code": "RUNTIME_NOT_READY",
                "message": str(exc),
            },
        }
    return jsonify({
        "upcoming_count": len(_upcoming_matches_cache),
        "teams_count": len(_teams_cache),
        "fetch_errors": _fetch_errors,
        "data_available": len(_upcoming_matches_cache) > 0,
        "runtime": runtime,
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
        home = _repository.resolve_team_unique(team_a)
        away = _repository.resolve_team_unique(team_b)
        h2h = {"total_matches": 0}
        if home and away and home["team_type"] == away["team_type"]:
            group = _runtime_manager.current().team_type_models.get(home["team_type"])
            if group:
                h2h = group.models["head_to_head"].get_h2h(
                    home["canonical_name"], away["canonical_name"]
                )
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
    home_team_id = str(data.get("home_team_id") or "").strip()
    away_team_id = str(data.get("away_team_id") or "").strip()
    if not (home_team or home_team_id) or not (away_team or away_team_id):
        raise PredictionInputError("请选择主队和客队", "MISSING_TEAMS")
    if (home_team and home_team == away_team) or (
        home_team_id and home_team_id == away_team_id
    ):
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
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,
        "competition_id": str(data.get("competition_id") or ""),
        "league_cn": league_cn,
        "league": LEAGUES.get(league_cn, "world_cup"),
        "neutral": neutral,
        "home_missing": home_missing,
        "away_missing": away_missing,
        "home_odds": odds[0],
        "draw_odds": odds[1],
        "away_odds": odds[2],
        "task_id": str(data.get("task_id") or "default"),
        "predicted_at": data.get("predicted_at"),
        "match_id": data.get("match_id"),
        "odds_captured_at": data.get("odds_captured_at"),
    }


def _run_predictions(context, report_progress=None):
    request_payload = {
        "home_team": context.get("home_team"),
        "away_team": context.get("away_team"),
        "home_team_id": context.get("home_team_id"),
        "away_team_id": context.get("away_team_id"),
        "competition_id": context.get("competition_id"),
        "league": context.get("league_cn"),
        "neutral": context.get("neutral"),
        "home_missing": context.get("home_missing", []),
        "away_missing": context.get("away_missing", []),
        "home_odds": context.get("home_odds"),
        "draw_odds": context.get("draw_odds"),
        "away_odds": context.get("away_odds"),
        "predicted_at": context.get("predicted_at"),
        "match_id": context.get("match_id"),
        "odds_captured_at": context.get("odds_captured_at"),
    }
    prediction_request = _prediction_service.request_from_payload(request_payload)
    result = _prediction_service.predict(
        prediction_request,
        include_trace=bool(context.get("include_trace")),
        progress=report_progress,
    ).to_dict()
    home = _repository.get_team(prediction_request.home_team_id)
    away = _repository.get_team(prediction_request.away_team_id)
    competition = _repository.get_competition(prediction_request.competition_id)
    result.update({
        "home_team": home["canonical_name"],
        "away_team": away["canonical_name"],
        "neutral": prediction_request.neutral,
        "league": competition["canonical_name"],
    })
    return result

@app.route("/predict", methods=["POST"])
def predict():
    try:
        _init_models()
    except Exception as exc:
        print(f"[Runtime] initialization failed: {exc}")
        return _api_error("预测运行时尚未就绪", "RUNTIME_NOT_READY", 503)
    try:
        context = _parse_prediction_input(request.get_json(silent=True), default_neutral=True)
    except PredictionInputError as exc:
        return _api_error(str(exc), exc.code, 400)

    task_id = context["task_id"]
    _prediction_progress[task_id] = {"total": 12, "done": 0, "current": "准备中..."}
    def report(n,name):
        if task_id in _prediction_progress:
            _prediction_progress[task_id]["done"]=n
            _prediction_progress[task_id]["current"]=name
    try:
        result = _run_predictions(context, report)
    except InvalidPredictionRequestError as exc:
        return _api_error(str(exc), exc.code, 400)
    except SnapshotTimeMismatchError as exc:
        return _api_error(str(exc), exc.code, 409)
    except NoAvailableModelsError as exc:
        return _api_error(str(exc), "NO_AVAILABLE_MODELS", 503)
    except (RuntimeNotReadyError, ModelExecutionError) as exc:
        return _api_error("预测服务暂时不可用", exc.code, 503 if isinstance(exc, RuntimeNotReadyError) else 500)
    except Exception as exc:
        print(f"[Predict] unexpected failure: {exc}")
        return _api_error("预测服务内部错误", "INTERNAL_ERROR", 500)

    result.setdefault("confidence", result.get("model_agreement", 0.0))
    return jsonify(_convert_numpy(result))

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
        _upcoming_matches_cache = _normalize_upcoming(d.get("upcoming", []))
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
        _init_models()
        context = _parse_prediction_input(
            request.get_json(silent=True), default_neutral=False
        )
        context["include_trace"] = True
        result = _run_predictions(context)
        trace = result.pop("trace", {}) or {}
        debug = dict(result)
        debug["raw_data"] = trace.get("raw_data", {})
        debug["calculation_steps"] = trace.get("calculation_steps", {})
        debug["features"] = trace.get("features", {})
        debug["model_outputs"] = {
            key: {
                "home_win": value.get("home_win"),
                "draw": value.get("draw"),
                "away_win": value.get("away_win"),
                "expected_goals": value.get("expected_total_goals"),
                "available": value.get("available"),
                "status": value.get("status"),
            }
            for key, value in result.get("predictions", {}).items()
        }
        debug["weights"] = result.get("ensemble", {}).get("effective_weights", {})
        return jsonify(_convert_numpy(debug))
    except PredictionInputError as exc:
        return _api_error(str(exc), exc.code, 400)
    except InvalidPredictionRequestError as exc:
        return _api_error(str(exc), exc.code, 400)
    except SnapshotTimeMismatchError as exc:
        return _api_error(str(exc), exc.code, 409)
    except NoAvailableModelsError as exc:
        return _api_error(str(exc), "NO_AVAILABLE_MODELS", 503)
    except (RuntimeNotReadyError, ModelExecutionError) as exc:
        return _api_error("预测服务暂时不可用", exc.code, 503 if isinstance(exc, RuntimeNotReadyError) else 500)
    except Exception as exc:
        print(f"[Debug] unexpected failure: {exc}")
        return _api_error("预测服务内部错误", "INTERNAL_ERROR", 500)

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
    _init_models()
    team_a = request.args.get("a", "")
    team_b = request.args.get("b", "")
    home = _repository.resolve_team_unique(team_a)
    away = _repository.resolve_team_unique(team_b)
    snapshot = _runtime_manager.current()
    h2h = []
    if home and away and home["team_type"] == away["team_type"]:
        group = snapshot.team_type_models.get(home["team_type"])
        if group:
            names = {home["canonical_name"], away["canonical_name"]}
            h2h = [
                dict(match) for match in group.matches
                if {match.get("home_team"), match.get("away_team")} == names
            ]
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
    return jsonify({
        "status": "unavailable",
        "error_code": "CALIBRATION_DISABLED_PENDING_BACKTEST",
        "message": "可信回测完成前不加载旧校准结果",
    })

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
    _init_models()
    snapshot = _runtime_manager.current()
    all_rankings = []
    for group in snapshot.team_type_models.values():
        all_rankings.extend(group.models["elo"].get_league_rankings())
    all_rankings.sort(key=lambda item: item[1], reverse=True)
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
    """兼容入口：使用完整运行时原子刷新。"""
    _init_models()
    return _runtime_manager.refresh("history_change")

@app.route("/api/sync_fifa", methods=["POST"])
@_admin_required
def api_sync_fifa():
    """同步FIFA世界杯比赛数据"""
    from data.fifa_sync import fetch_recent_fifa_source_records
    from data.match_repository import RepositoryNotInitializedError

    try:
        _ensure_runtime_components()
        _repository.get_data_quality_report()
        fetched = fetch_recent_fifa_source_records(days=14)

        def import_records():
            sync_run_id = _repository.create_sync_run("fifa_recent", {"days": 14})
            counts = _repository.import_source_records(
                fetched["records"], sync_run_id, sync_type="fifa_recent"
            )
            return {"sync_run_id": sync_run_id, **counts}

        imported, refresh = _runtime_manager.run_update(import_records, "fifa_recent")
        if imported is None:
            return _api_error("FIFA 数据同步失败", "FIFA_SYNC_FAILED", 500)

        return jsonify({
            "status": (
                "partial" if fetched["errors"] or refresh.status != "ok" else "ok"
            ),
            **imported,
            "fetched": fetched["fetched"],
            "errors": fetched["errors"],
            "total_history": len(_repository.list_matches({"status": "finished"})),
            "runtime_refresh": refresh.to_dict(),
        })
    except RuntimeRefreshInProgressError as exc:
        return _api_error(str(exc), exc.code, 409)
    except RepositoryNotInitializedError:
        return _api_error(
            "比赛数据库尚未初始化，请先执行历史数据迁移",
            "MATCH_REPOSITORY_NOT_INITIALIZED",
            503,
        )
    except Exception as e:
        print(f"[FIFA] 同步失败: {e}")
        return _api_error("FIFA 数据同步失败", "FIFA_SYNC_FAILED", 500)

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
    return _api_error(
        "可信回测完成前校准功能暂不可用",
        "CALIBRATION_DISABLED_PENDING_BACKTEST",
        503,
    )
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
    return jsonify({
        "status": "unavailable",
        "error_code": "CALIBRATION_DISABLED_PENDING_BACKTEST",
        "progress": 0,
        "done": True,
    })

@app.route("/api/calibrate/report")
def api_calibrate_report():
    return _api_error(
        "可信回测报告尚未生成",
        "CALIBRATION_DISABLED_PENDING_BACKTEST",
        404,
    )

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
