import math

from models.market_odds import MarketOddsModel
from models.massey import MasseyRanking


def test_massey_probabilities_remain_normalized_after_clipping():
    model = MasseyRanking()
    model.ratings = {"强队": 10.0, "弱队": -10.0}

    result = model.predict("强队", "弱队", neutral=True)

    assert all(0.0 <= result[key] <= 1.0 for key in ("home_win", "draw", "away_win"))
    assert math.isclose(
        result["home_win"] + result["draw"] + result["away_win"],
        1.0,
        abs_tol=1e-9,
    )


def test_market_model_is_unavailable_without_real_odds():
    result = MarketOddsModel().predict()

    assert result["available"] is False
    assert result["status"] == "no_market_odds"
    assert "market_odds_missing" in result["warnings"]
    assert result.get("home_win") is None


def test_market_model_rejects_invalid_odds():
    result = MarketOddsModel().predict(
        home_odds=1.0,
        draw_odds=3.2,
        away_odds=4.1,
    )

    assert result["available"] is False
    assert result["status"] == "invalid_odds"
