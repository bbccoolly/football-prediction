# main.py - CLI 主入口

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

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
from config import HOME_ADVANTAGE


def print_banner():
    print()
    print("=" * 60)
    print("  Soccer Prediction System v2.0")
    print("  12 Algorithms | Bayesian Ensemble | World Cup")
    print("=" * 60)
    print()


def print_prediction(predictions, ensemble, confidence):
    ens = ensemble
    print()
    print("-" * 50)
    print(f"  Ensemble  (confidence: {confidence:.0f}%)")
    print("-" * 50)
    print(f"  Home: {ens['home_win']*100:5.1f}%  |  Draw: {ens['draw']*100:5.1f}%  |  Away: {ens['away_win']*100:5.1f}%")
    print(f"  Expected goals: {ens['expected_total_goals']:.1f}")
    print()
    print("  Top scores:")
    for score, prob in ens.get("top_scores", [])[:5]:
        bar = "#" * int(prob * 200)
        print(f"    {score}  {prob*100:5.1f}%  {bar}")
    print()
    print("-" * 50)
    print(f"  {'Model':<16} {'Home':>8} {'Draw':>8} {'Away':>8} {'Goals':>6} {'Weight':>8}")
    print("  " + "-" * 56)
    model_names = {
        "poisson":"Poisson","dixon_coles":"Dixon-Coles","elo":"ELO",
        "massey":"Massey","form":"Form","head_to_head":"H2H",
        "market_odds":"Market","knn_similar":"KNN",
        "xgboost":"XGBoost","neural_net":"NN",
        "monte_carlo":"MonteCarlo","bayesian":"Bayesian",
    }
    weights = ens.get("weights", {})
    for key, pred in predictions.items():
        w = weights.get(key, 0)
        goals = pred.get("expected_total_goals", 0)
        print(f"  {model_names.get(key, key):<16} {pred['home_win']*100:7.1f}% {pred['draw']*100:7.1f}% {pred['away_win']*100:7.1f}% {goals:5.1f} {w*100:7.1f}%")
    print()


def run_interactive():
    print_banner()
    print("[Init] Loading models...")
    import random
    rng = random.Random(42)
    sample_teams = ["Argentina","France","Brazil","England","Portugal","Spain","Germany","Italy","Netherlands","Croatia"]
    sample_matches = []
    for _ in range(100):
        h = rng.choice(sample_teams); a = rng.choice(sample_teams)
        if h == a: continue
        sample_matches.append({"home_team":h,"away_team":a,"home_goals":rng.choices([0,1,2,3,4],weights=[8,20,25,15,7])[0],"away_goals":rng.choices([0,1,2,3,4],weights=[10,22,23,12,5])[0]})

    elo = EloRating(); elo.load()
    strengths = build_strengths_from_results(sample_matches)
    poisson = PoissonModel(); poisson.set_team_strengths(strengths)
    dc = DixonColesModel(); dc.set_team_strengths(strengths)
    massey = MasseyRanking(); massey.fit(sample_matches)
    form = FormModel(); form.load_history(sample_matches)
    h2h = HeadToHeadModel(); h2h.load_history(sample_matches)
    market = MarketOddsModel()
    knn = KNNSimilarModel()
    for m in sample_matches:
        fv = knn.feature_vector(1.0,1.0,1.0,1.0, 0.5,0.5, elo.get_rating(m["home_team"]),elo.get_rating(m["away_team"]), elo.get_rating(m["home_team"])-elo.get_rating(m["away_team"]))
        knn.add_match(fv, m["home_goals"], m["away_goals"])
    xgb = XGBoostModel(); nn = NeuralNetModel()
    mc = MonteCarloModel()
    bayes = BayesianHierarchicalModel(); bayes.fit(sample_matches)
    bma = BayesianModelAveraging()

    print("[Init] Done!")
    print()

    while True:
        print("-" * 50)
        home = input("  Home team (q quit): ").strip()
        if home.lower() == 'q': break
        away = input("  Away team: ").strip()
        if not home or not away: continue
        if home == away: print("  Cannot be same"); continue
        neutral = input("  Neutral? (y/n): ").strip().lower() == 'y'

        print(f"  {home} vs {away}")
        predictions = {}
        predictions["poisson"] = poisson.predict(home, away, neutral)
        predictions["dixon_coles"] = dc.predict(home, away, neutral)
        predictions["elo"] = elo.predict_match(home, away, neutral)
        predictions["massey"] = massey.predict(home, away, neutral)
        predictions["form"] = form.predict(home, away, neutral)
        predictions["head_to_head"] = h2h.predict(home, away, neutral)
        predictions["market_odds"] = market.predict()
        fq = knn.feature_vector(
            poisson.attack_strengths.get(home,1.0),poisson.defense_strengths.get(home,1.0),
            poisson.attack_strengths.get(away,1.0),poisson.defense_strengths.get(away,1.0),
            form.get_form_score(home)["form_score"],form.get_form_score(away)["form_score"],
            elo.get_rating(home),elo.get_rating(away),elo.get_rating(home)-elo.get_rating(away))
        predictions["knn_similar"] = knn.predict(fq)
        predictions["xgboost"] = xgb.predict(fq)
        predictions["neural_net"] = nn.predict(fq)
        preds_mc = list(predictions.values())
        w_mc = [bma.get_weights().get(k,0.08) for k in predictions]
        predictions["monte_carlo"] = mc.simulate(preds_mc, w_mc)
        predictions["bayesian"] = bayes.predict(home, away, neutral)
        blend = bma.blend(predictions)

        home_p = [p["home_win"] for p in predictions.values()]
        draw_p = [p["draw"] for p in predictions.values()]
        away_p = [p["away_win"] for p in predictions.values()]
        import math
        std = lambda vs: math.sqrt(sum((x-sum(vs)/len(vs))**2 for x in vs)/len(vs))
        conf = max(0,min(100,100*(1.0-(std(home_p)+std(draw_p)+std(away_p))/3*5)))
        print_prediction(predictions, blend, conf)


def run_web():
    from web.app import app, _init_models
    _init_models()
    print("\n" + "=" * 60)
    print("  Soccer Prediction System v2.0")
    print("  Open: http://127.0.0.1:5000")
    print("=" * 60)
    app.run(debug=False, host="127.0.0.1", port=5000)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "web":
        run_web()
    else:
        run_interactive()
