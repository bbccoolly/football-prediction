"""足球预测系统命令行入口。"""

from data.match_repository import get_default_repository
from prediction import ModelRuntimeBuilder, PredictionService, RuntimeManager


MODEL_NAMES = {
    "poisson": "Poisson", "dixon_coles": "Dixon-Coles", "elo": "ELO",
    "massey": "Massey", "form": "近期状态", "head_to_head": "历史交锋",
    "market_odds": "市场赔率", "knn_similar": "KNN",
    "xgboost": "XGBoost", "neural_net": "神经网络",
    "monte_carlo": "Monte Carlo", "bayesian": "Bayesian",
}


def print_banner():
    print()
    print("=" * 60)
    print("  足球预测系统 v2.1")
    print("  共享预测服务 | 分域模型 | 原子快照")
    print("=" * 60)
    print()


def print_prediction(result):
    ensemble = result["ensemble"]
    print()
    print("-" * 60)
    print(
        f"  融合结果：主胜 {ensemble['home_win'] * 100:5.1f}% | "
        f"平局 {ensemble['draw'] * 100:5.1f}% | "
        f"客胜 {ensemble['away_win'] * 100:5.1f}%"
    )
    print(f"  预期总进球：{ensemble['expected_total_goals']:.2f}")
    print(f"  模型一致度：{result['model_agreement']:.1f}%")
    print("-" * 60)
    for model_id, prediction in result["predictions"].items():
        name = MODEL_NAMES.get(model_id, model_id)
        if not prediction.get("available"):
            print(f"  {name:<16} 不可用 ({prediction.get('status', 'unknown')})")
            continue
        weight = ensemble.get("effective_weights", {}).get(model_id, 0.0)
        print(
            f"  {name:<16} {prediction['home_win'] * 100:5.1f}% / "
            f"{prediction['draw'] * 100:5.1f}% / "
            f"{prediction['away_win'] * 100:5.1f}%  权重 {weight * 100:5.1f}%"
        )
    print(f"  快照：{result['runtime_snapshot_id']}")


def create_service():
    repository = get_default_repository()
    manager = RuntimeManager(ModelRuntimeBuilder(repository))
    manager.initialize()
    return PredictionService(repository, manager)


def run_interactive():
    print_banner()
    print("[初始化] 正在构建预测运行时...")
    service = create_service()
    print("[初始化] 完成")
    while True:
        print()
        home = input("主队（输入 q 退出）：").strip()
        if home.lower() == "q":
            break
        away = input("客队：").strip()
        league = input("赛事（默认世界杯）：").strip() or "世界杯"
        neutral = input("是否中立场（y/N）：").strip().lower() == "y"
        try:
            request = service.request_from_payload({
                "home_team": home,
                "away_team": away,
                "league": league,
                "neutral": neutral,
            })
            print_prediction(service.predict(request).to_dict())
        except Exception as exc:
            print(f"预测失败：{exc}")


def run_web():
    from web.app import app, _init_models

    _init_models()
    print("访问 http://127.0.0.1:5000")
    app.run(debug=False, host="127.0.0.1", port=5000)


if __name__ == "__main__":
    import sys

    run_web() if len(sys.argv) > 1 and sys.argv[1] == "web" else run_interactive()
