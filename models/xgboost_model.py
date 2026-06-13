# models/xgboost_model.py - XGBoost 集成学习

import os
import numpy as np
from config import MODEL_DIR


class XGBoostModel:
    """XGBoost 分类+回归：胜平负三分类 + 总进球回归"""

    def __init__(self):
        self.classifier = None   # 胜平负分类器
        self.regressor = None    # 总进球回归器
        self.is_trained = False
        self.feature_names = None

    def build_features(self, data: dict) -> np.ndarray:
        """data 包含所有特征字段，返回特征向量"""
        features_order = [
            "elo_diff", "massey_diff", "form_diff",
            "home_attack", "home_defense", "away_attack", "away_defense",
            "h2h_home_win_rate", "h2h_draw_rate", "h2h_avg_goals",
            "home_ppg", "away_ppg", "home_gd_avg", "away_gd_avg",
            "squad_completeness_home", "squad_completeness_away",
            "home_advantage", "neutral",
        ]
        self.feature_names = features_order
        vec = []
        for f in features_order:
            vec.append(float(data.get(f, 0)))
        return np.array(vec)

    def fit(self, X: np.ndarray, y_result: np.ndarray, y_goals: np.ndarray):
        """训练模型
        y_result: 0=主胜, 1=平局, 2=客胜
        y_goals: 总进球数
        """
        try:
            from xgboost import XGBClassifier, XGBRegressor
        except ImportError:
            print("[XGBoost] xgboost not installed, skipping training")
            return

        self.classifier = XGBClassifier(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            objective="multi:softprob", num_class=3,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbosity=0,
        )
        self.regressor = XGBRegressor(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, verbosity=0,
        )

        self.classifier.fit(X, y_result)
        self.regressor.fit(X, y_goals)
        self.is_trained = True

    def predict(self, features: np.ndarray) -> dict:
        """预测单场比赛"""
        if not self.is_trained or self.classifier is None:
            return {
                "model": "xgboost",
                "home_win": 0.38, "draw": 0.28, "away_win": 0.34,
                "expected_total_goals": 2.7,
                "status": "not_trained",
            }

        X = features.reshape(1, -1)
        proba = self.classifier.predict_proba(X)[0]

        return {
            "model": "xgboost",
            "home_win": round(float(proba[0]), 4),
            "draw": round(float(proba[1]), 4),
            "away_win": round(float(proba[2]), 4),
            "expected_total_goals": round(float(self.regressor.predict(X)[0]), 2),
        }

    def save(self, name: str = "xgboost"):
        os.makedirs(MODEL_DIR, exist_ok=True)
        try:
            import joblib
            joblib.dump(self.classifier, os.path.join(MODEL_DIR, f"{name}_clf.pkl"))
            joblib.dump(self.regressor, os.path.join(MODEL_DIR, f"{name}_reg.pkl"))
        except Exception as e:
            print(f"[XGBoost] Save failed: {e}")

    def load(self, name: str = "xgboost"):
        try:
            import joblib
            clf_path = os.path.join(MODEL_DIR, f"{name}_clf.pkl")
            reg_path = os.path.join(MODEL_DIR, f"{name}_reg.pkl")
            if os.path.exists(clf_path) and os.path.exists(reg_path):
                self.classifier = joblib.load(clf_path)
                self.regressor = joblib.load(reg_path)
                self.is_trained = True
        except Exception as e:
            print(f"[XGBoost] Load failed: {e}")
            self.is_trained = False
