# models/neural_net.py - 简单神经网络预测

import os
import numpy as np
from config import MODEL_DIR


class NeuralNetModel:
    """2-3 层全连接神经网络：胜平负概率预测"""

    def __init__(self, input_dim: int = 16, hidden_dims: list = None):
        self.input_dim = input_dim
        self.hidden_dims = hidden_dims or [32, 16]
        self.model = None
        self.is_trained = False
        self.scaler_mean = None
        self.scaler_std = None

    def _build_model(self):
        """构建简单的 numpy 前馈网络"""
        dims = [self.input_dim] + self.hidden_dims + [3]  # 3 输出：胜/平/负
        weights = []
        biases = []

        for i in range(len(dims) - 1):
            # Xavier 初始化
            scale = np.sqrt(2.0 / (dims[i] + dims[i + 1]))
            w = np.random.randn(dims[i], dims[i + 1]) * scale
            b = np.zeros(dims[i + 1])
            weights.append(w)
            biases.append(b)

        return weights, biases

    def _relu(self, x):
        return np.maximum(0, x)

    def _softmax(self, x):
        e_x = np.exp(x - np.max(x, axis=1, keepdims=True))
        return e_x / np.sum(e_x, axis=1, keepdims=True)

    def _forward(self, X, weights, biases):
        activations = [X]
        for i in range(len(weights) - 1):
            z = activations[-1] @ weights[i] + biases[i]
            activations.append(self._relu(z))
        # 最后一层 softmax
        z = activations[-1] @ weights[-1] + biases[-1]
        return self._softmax(z), activations

    def fit(self, X: np.ndarray, y: np.ndarray, epochs: int = 500,
            lr: float = 0.01, batch_size: int = 32, verbose: bool = False):
        """训练网络
        X: (n_samples, n_features)
        y: (n_samples,) 整数标签 0=主胜, 1=平, 2=客胜
        """
        # 标准化
        self.scaler_mean = X.mean(axis=0)
        self.scaler_std = X.std(axis=0) + 1e-8
        X_norm = (X - self.scaler_mean) / self.scaler_std

        # One-hot encode y
        n = len(y)
        y_onehot = np.zeros((n, 3))
        y_onehot[np.arange(n), y] = 1.0

        self.weights, self.biases = self._build_model()

        for epoch in range(epochs):
            # Mini-batch SGD
            indices = np.random.permutation(n)
            for start in range(0, n, batch_size):
                batch_idx = indices[start:start + batch_size]
                X_batch = X_norm[batch_idx]
                y_batch = y_onehot[batch_idx]

                # Forward
                probs, activations = self._forward(X_batch, self.weights, self.biases)

                # Backward (simplified: cross-entropy + softmax gradient)
                grad = probs - y_batch
                m = len(X_batch)

                # Backprop through layers
                for i in range(len(self.weights) - 1, -1, -1):
                    dw = activations[i].T @ grad / m
                    db = grad.sum(axis=0) / m
                    self.weights[i] -= lr * dw
                    self.biases[i] -= lr * db

                    if i > 0:
                        grad = (grad @ self.weights[i].T) * (activations[i] > 0)

            if verbose and epoch % 100 == 0:
                probs_full, _ = self._forward(X_norm, self.weights, self.biases)
                loss = -np.mean(np.sum(y_onehot * np.log(probs_full + 1e-8), axis=1))
                acc = np.mean(np.argmax(probs_full, axis=1) == y)
                print(f"  Epoch {epoch:4d} | loss={loss:.4f} | acc={acc:.3f}")

        self.is_trained = True

    def predict(self, features: np.ndarray) -> dict:
        if not self.is_trained:
            return {
                "model": "neural_net",
                "home_win": 0.38, "draw": 0.28, "away_win": 0.34,
                "status": "not_trained",
            }

        X = (features.reshape(1, -1) - self.scaler_mean) / self.scaler_std
        probs, _ = self._forward(X, self.weights, self.biases)

        return {
            "model": "neural_net",
            "home_win": round(float(probs[0][0]), 4),
            "draw": round(float(probs[0][1]), 4),
            "away_win": round(float(probs[0][2]), 4),
        }

    def save(self, name: str = "neural_net"):
        os.makedirs(MODEL_DIR, exist_ok=True)
        np.savez(
            os.path.join(MODEL_DIR, f"{name}.npz"),
            *self.weights,
            *self.biases,
            scaler_mean=self.scaler_mean,
            scaler_std=self.scaler_std,
        )

    def load(self, name: str = "neural_net"):
        path = os.path.join(MODEL_DIR, f"{name}.npz")
        if not os.path.exists(path):
            return
        data = np.load(path, allow_pickle=True)
        files = list(data.keys())
        n_layers = (len(files) - 2) // 2  # 减去 scaler_mean, scaler_std
        self.weights = [data[f"arr_{i}"] for i in range(n_layers)]
        self.biases = [data[f"arr_{i + n_layers}"] for i in range(n_layers)]
        self.scaler_mean = data["arr_" + str(2 * n_layers)].item() if f"arr_{2 * n_layers}" in data else None
        self.scaler_std = data["arr_" + str(2 * n_layers + 1)].item() if f"arr_{2 * n_layers + 1}" in data else None
        self.is_trained = True
