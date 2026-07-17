# bet_jczq.py — 竞彩足球智能分析投注工具 · 主入口
"""用法:
  python bet_jczq.py              # 用示例数据演示
  python bet_jczq.py --live       # 实时抓取500.com数据
  python bet_jczq.py --quick      # 快速模式(缓存+示例兜底)
  python bet_jczq.py --output plan.txt  # 输出到文件
"""

import sys
import os
import json

# 确保项目根在 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from betting.jczq_engine import JczqEngine
from betting.jczq_planner import JczqPlanner, format_plan
from betting.jczq_fetcher import SAMPLE_MATCHES, fetch_all_odds


def print_banner():
    print()
    print("=" * 68)
    print("  竞彩足球智能分析投注工具 v1.0")
    print("  算法: Poisson(55%) + Elo(15%) + FIFA(30%) + 冷门因子")
    print("  策略: +EV筛选 | 100元最优分配 | 单关/串关约束")
    print("=" * 68)
    print()


def run(matches: list, output_file=None, upset=0.12, budget=100.0) -> dict:
    """主运行流程"""
    engine = JczqEngine(upset_factor=upset, is_national=True)
    planner = JczqPlanner(engine=engine, budget=budget, unit_price=2.0)

    plan = planner.generate_plan(matches)

    # 格式化输出
    output = format_plan(plan)
    print(output)

    # 保存到文件
    if output_file:
        with open(output_file, "w", encoding="utf-8") as f:
            f.write(output)
        print(f"\n[保存] 方案已写入: {output_file}")

    return plan


def main():
    args = sys.argv[1:]

    output_file = None
    use_live = False
    upset = 0.12

    i = 0
    while i < len(args):
        if args[i] == "--live":
            use_live = True
        elif args[i] == "--quick":
            use_live = False
        elif args[i] == "--output" and i + 1 < len(args):
            output_file = args[i + 1]
            i += 1
        elif args[i] == "--upset" and i + 1 < len(args):
            upset = float(args[i + 1])
            i += 1
        elif args[i] == "--help" or args[i] == "-h":
            print(__doc__)
            return
        i += 1

    print_banner()

    # 获取比赛数据
    if use_live:
        print("[模式] 实时抓取 500.com 数据...")
        try:
            data = fetch_all_odds(force_refresh=True)
            matches = data.get("matches", [])
            if not matches:
                print("[警告] 未抓取到比赛，使用示例数据")
                matches = SAMPLE_MATCHES
        except Exception as e:
            print(f"[错误] 抓取失败: {e}")
            print("[回退] 使用示例数据")
            matches = SAMPLE_MATCHES
    else:
        # 先尝试缓存
        try:
            data = fetch_all_odds(force_refresh=False, use_cache=True)
            matches = data.get("matches", [])
            if matches:
                print(f"[数据] 缓存: {len(matches)} 场比赛")
            else:
                print("[数据] 使用示例数据 (4场比赛)")
                matches = SAMPLE_MATCHES
        except:
            print("[数据] 使用示例数据 (4场比赛)")
            matches = SAMPLE_MATCHES

    print(f"[参数] 冷门因子={upset}, 本金=100元, 单注=2元\n")

    # 运行分析
    plan = run(matches, output_file=output_file, upset=upset, budget=budget)

    # 打印额外统计
    bets = plan["bets"]
    if bets:
        single_count = len([b for b in bets if not b["parlay_only"]])
        parlay_count = len([b for b in bets if b["parlay_only"]])
        print(f"\n  [OK] 单关投注: {single_count}项 | 串关投注: {parlay_count}项")
        print(f"  [OK] 总注数: {sum(b['zhu'] for b in bets)} | 总金额: {plan['total_stake']:.0f}元")

        top_ev = max(bets, key=lambda b: b["ev_pct"])
        print(f"  [OK] 最高EV: {top_ev['desc']} ({top_ev['ev_pct']:+.1f}%)")
    else:
        print("\n  [WARN] 未找到+EV选项，建议本轮观望不投")

    return plan


if __name__ == "__main__":
    main()

