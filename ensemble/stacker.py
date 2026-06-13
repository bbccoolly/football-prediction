# ensemble/stacker.py - Stacking 元学习器

import os
import numpy as np
from config import MODEL_DIR, STACKING_CV_FOLDS


class StackingEnsemble:
    """Stacking：用元学习器学习各模型的预测与最终结果之间的映射"""

    def __init__(self):
        self.meta_model = None
        self.is_trained = False
        self.model_names = None

    def fit(self, model_predictions: np.ndarray, y: np.ndarray):
        """训练元学习器
        model_predictions: (n_samples, n_models * 3)  每模型输出 [home, draw, away]
        y: (n_samples,) 0=主胜, 1=平, 2=客胜
        """
        try:
            from sklearn.linear_model import LogisticRegression
        except ImportError:
            print("[Stacking] sklearn not available")
            return

        self.meta_model = LogisticRegression(
            multi_class="multinomial", max_iter=1000, C=1.0,
            solver="lbfgs", random_state=42,
        )
        self.meta_model.fit(model_predictions, y)
        self.is_trained = True

    def predict(self, model_outputs: np.ndarray) -> dict:
        """预测：model_outputs 为单场比赛各模型输出展平向量"""
        if not self.is_trained:
            return {
                "home_win": 0.38, "draw": 0.28, "away_win": 0.34,
                "status": "not_trained",
            }

        X = model_outputs.reshape(1, -1)
        proba = self.meta_model.predict_proba(X)[0]

        return {
            "home_win": round(float(proba[0]), 4),
            "draw": round(float(proba[1]), 4),
            "away_win": round(float(proba[2]), 4),
        }

    def save(self, name: str = "stacker"):
        os.makedirs(MODEL_DIR, exist_ok=True)
        try:
            import joblib
            joblib.dump(self.meta_model, os.path.join(MODEL_DIR, f"{name}.pkl"))
        except Exception as e:
            print(f"[Stacking] Save failed: {e}")

    def load(self, name: str = "stacker"):
        try:
            import joblib
            path = os.path.join(MODEL_DIR, f"{name}.pkl")
            if os.path.exists(path):
                self.meta_model = joblib.load(path)
                self.is_trained = True
        except Exception as e:
            print(f"[Stacking] Load failed: {e}")
            self.is_trained = False
