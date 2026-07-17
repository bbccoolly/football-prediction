# lottery_predictor.py — 彩票号码智能预测引擎 v2
"""
基于历史数据的自学习预测系统。
特性:
  1. 时间衰减加权频率（近期数据权重更高）
  2. 5种策略并行：热号追踪、冷号反弹、遗漏回补、模式匹配、综合贝叶斯
  3. 回测验证：留出最近N期测试各策略命中率
  4. 自适应选策：自动选用回测胜率最高的策略
  5. 模式分析：奇偶比、大小比、和值区间、连号检测
"""

import random
import math
from collections import Counter, defaultdict


# ============================================================
# 策略权重（基于 2026-07-03 回测修正）
# 每次开奖后可更新此配置
# ============================================================
LEARNED_WEIGHTS = {
    "dlt":  {"hot": 5, "pattern": 5, "weighted": 2, "cold": 1, "missing": 1},
    "qxc":  {"missing": 5, "cold": 3, "weighted": 2, "hot": 2, "pattern": 2},
    "pls":  {"cold": 5, "weighted": 3, "pattern": 2, "hot": 1, "missing": 1},
    "plw":  {"weighted": 5, "cold": 3, "pattern": 3, "hot": 2, "missing": 1},
}

# ============================================================
# 彩种参数
# ============================================================
LOTTERY_PARAMS = {
    "dlt": {
        "name": "大乐透",
        "zones": [
            {"name": "前区", "range": (1, 35), "count": 5},
            {"name": "后区", "range": (1, 12), "count": 2},
        ],
    },
    "qxc": {
        "name": "星彩",
        "positions": 7,
        "range_per_pos": (0, 9),
    },
    "pls": {
        "name": "排列三",
        "positions": 3,
        "range_per_pos": (0, 9),
    },
    "plw": {
        "name": "排列五",
        "positions": 5,
        "range_per_pos": (0, 9),
    },
}

# ============================================================
# 1. 时间衰减加权频率
# ============================================================
HALF_LIFE = 12  # 半衰期：12期前的数据权重减半

def _decay_weight(periods_ago: int) -> float:
    """指数衰减权重：越近的期数权重越高"""
    return math.exp(-math.log(2) * periods_ago / HALF_LIFE)


def _decayed_counter(history: list, lottery_key: str) -> dict:
    """为每个号码计算时间衰减加权频率"""
    params = LOTTERY_PARAMS.get(lottery_key)
    if not params:
        return {}

    result = {}

    if "zones" in params:
        for zi, zone in enumerate(params["zones"]):
            start_idx = sum(z["count"] for z in params["zones"][:zi])
            end_idx = start_idx + zone["count"]
            total_weight = 0.0
            weighted = defaultdict(float)

            for i, draw in enumerate(history):
                periods_ago = len(history) - 1 - i
                w = _decay_weight(periods_ago)
                total_weight += w
                nums = draw.get("numbers", [])
                if len(nums) > end_idx:
                    for n in nums[start_idx:end_idx]:
                        weighted[n] += w

            fmt = lambda n: str(n).zfill(2) if zone["range"][1] >= 10 else str(n)
            result[zone["name"]] = {
                fmt(n): weighted.get(fmt(n), 0.0) / max(total_weight, 0.001)
                for n in range(zone["range"][0], zone["range"][1] + 1)
            }

    elif "positions" in params:
        n_pos = params["positions"]
        lo, hi = params["range_per_pos"]
        result["positions"] = [defaultdict(float) for _ in range(n_pos)]
        pos_total_w = [0.0] * n_pos

        for i, draw in enumerate(history):
            periods_ago = len(history) - 1 - i
            w = _decay_weight(periods_ago)
            nums = draw.get("numbers", [])
            for p in range(min(n_pos, len(nums))):
                result["positions"][p][nums[p]] += w
                pos_total_w[p] += w

        # 归一化
        for p in range(n_pos):
            tw = max(pos_total_w[p], 0.001)
            result["positions"][p] = {
                str(k): v / tw for k, v in result["positions"][p].items()
            }

    return result


# ============================================================
# 2. 多策略预测引擎
# ============================================================
def _strategy_hot(decayed: dict, lottery_key: str, params: dict) -> list:
    """策略1：热号追踪 - 选频率最高的号码"""
    if "zones" in params:
        picks = []
        for zi, zone in enumerate(params["zones"]):
            zn = zone["name"]
            freqs = decayed.get(zn, {})
            sorted_nums = sorted(freqs.items(), key=lambda x: -x[1])
            top = [n for n, _ in sorted_nums[:zone["count"] * 3]]
            selected = random.sample(top, min(zone["count"], len(top)))
            picks.extend(sorted(selected, key=lambda x: int(x)))
        return picks
    elif "positions" in params:
        pos_freqs = decayed.get("positions", [])
        picks = []
        for p in range(params["positions"]):
            freqs = pos_freqs[p] if p < len(pos_freqs) else {}
            if freqs:
                top = sorted(freqs.items(), key=lambda x: -x[1])[:3]
                picks.append(random.choice([n for n, _ in top]))
            else:
                picks.append(str(random.randint(0, 9)))
        return picks
    return []


def _strategy_cold(decayed: dict, lottery_key: str, params: dict) -> list:
    """策略2：冷号反弹 - 选频率最低的号码（赌反弹）"""
    if "zones" in params:
        picks = []
        for zi, zone in enumerate(params["zones"]):
            zn = zone["name"]
            freqs = decayed.get(zn, {})
            sorted_nums = sorted(freqs.items(), key=lambda x: x[1])
            bottom = [n for n, _ in sorted_nums[:zone["count"] * 3]]
            selected = random.sample(bottom, min(zone["count"], len(bottom)))
            picks.extend(sorted(selected, key=lambda x: int(x)))
        return picks
    elif "positions" in params:
        pos_freqs = decayed.get("positions", [])
        picks = []
        for p in range(params["positions"]):
            freqs = pos_freqs[p] if p < len(pos_freqs) else {}
            if freqs:
                bottom = sorted(freqs.items(), key=lambda x: x[1])[:3]
                picks.append(random.choice([n for n, _ in bottom]))
            else:
                picks.append(str(random.randint(0, 9)))
        return picks
    return []


def _strategy_weighted(decayed: dict, lottery_key: str, params: dict) -> list:
    """策略3：综合加权 - 按衰减频率加权随机抽样"""
    if "zones" in params:
        picks = []
        for zi, zone in enumerate(params["zones"]):
            zn = zone["name"]
            freqs = decayed.get(zn, {})
            items = list(freqs.items())
            nums_list = [n for n, _ in items]
            weights = [max(v, 0.001) for _, v in items]
            total = sum(weights)
            probs = [w / total for w in weights]

            selected = []
            remaining = list(range(len(nums_list)))
            rem_probs = list(probs)
            for _ in range(zone["count"]):
                if not remaining:
                    break
                rp_sum = sum(rem_probs)
                rp_norm = [p / rp_sum for p in rem_probs]
                idx = random.choices(range(len(remaining)), weights=rp_norm, k=1)[0]
                selected.append(nums_list[remaining[idx]])
                remaining.pop(idx)
                rem_probs.pop(idx)
            picks.extend(sorted(selected, key=lambda x: int(x)))
        return picks

    elif "positions" in params:
        pos_freqs = decayed.get("positions", [])
        picks = []
        for p in range(params["positions"]):
            freqs = pos_freqs[p] if p < len(pos_freqs) else {}
            if freqs:
                items = list(freqs.items())
                nums = [n for n, _ in items]
                w = [max(v, 0.001) for _, v in items]
                total = sum(w)
                probs = [x / total for x in w]
                picks.append(random.choices(nums, weights=probs, k=1)[0])
            else:
                picks.append(str(random.randint(0, 9)))
        return picks
    return []


def _strategy_missing(decayed: dict, lottery_key: str, params: dict, history: list = None) -> list:
    """策略4：遗漏回补 - 优先选最久未出的号码"""
    if history is None:
        history = []
    if "zones" in params:
        picks = []
        for zi, zone in enumerate(params["zones"]):
            start_idx = sum(z["count"] for z in params["zones"][:zi])
            end_idx = start_idx + zone["count"]
            fmt = lambda n: str(n).zfill(2) if zone["range"][1] >= 10 else str(n)

            # 计算每个号码的遗漏期数
            all_nums = set(fmt(n) for n in range(zone["range"][0], zone["range"][1] + 1))
            last_seen = {n: len(history) for n in all_nums}

            for i, draw in enumerate(history):
                nums = draw.get("numbers", [])
                if len(nums) > end_idx:
                    for n in nums[start_idx:end_idx]:
                        n_str = fmt(n)
                        periods = len(history) - 1 - i
                        if periods < last_seen.get(n_str, 999):
                            last_seen[n_str] = periods

            sorted_missing = sorted(last_seen.items(), key=lambda x: -x[1])
            candidates = [n for n, _ in sorted_missing[:zone["count"] * 3]]
            selected = random.sample(candidates, min(zone["count"], len(candidates)))
            picks.extend(sorted(selected, key=lambda x: int(x)))
        return picks

    elif "positions" in params:
        n_pos = params["positions"]
        picks = []
        for p in range(n_pos):
            last_seen = {str(i): len(history) for i in range(10)}
            for i, draw in enumerate(history):
                nums = draw.get("numbers", [])
                if nums and p < len(nums):
                    periods = len(history) - 1 - i
                    n = nums[p]
                    if periods < last_seen.get(n, 999):
                        last_seen[n] = periods
            sorted_m = sorted(last_seen.items(), key=lambda x: -x[1])
            candidates = [n for n, _ in sorted_m[:3]]
            if candidates:
                picks.append(random.choice(candidates))
            else:
                picks.append(str(random.randint(0, 9)))
        return picks
    return []


def _strategy_pattern(decayed: dict, lottery_key: str, params: dict, history: list = None) -> list:
    """策略5：模式匹配 - 分析奇偶/大小/和值模式后生成"""
    if history is None:
        history = []
    if "zones" in params:
        picks = []
        for zi, zone in enumerate(params["zones"]):
            start_idx = sum(z["count"] for z in params["zones"][:zi])
            end_idx = start_idx + zone["count"]
            lo, hi = zone["range"]
            mid = (lo + hi) // 2
            fmt = lambda n: str(n).zfill(2) if hi >= 10 else str(n)

            # 统计近期奇偶比偏好
            odd_count = 0
            big_count = 0
            total = 0
            for draw in history[-20:]:
                nums = draw.get("numbers", [])
                if len(nums) > end_idx:
                    for n in nums[start_idx:end_idx]:
                        total += 1
                        if int(n) % 2 == 1:
                            odd_count += 1
                        if int(n) > mid:
                            big_count += 1

            odd_ratio = odd_count / max(total, 1)
            big_ratio = big_count / max(total, 1)

            # 根据历史模式加权生成
            all_raw = list(range(lo, hi + 1))
            weights = []
            for n in all_raw:
                w = 1.0
                # 偏向匹配历史奇偶比
                if n % 2 == 1:
                    w *= (0.5 + odd_ratio)
                else:
                    w *= (0.5 + (1 - odd_ratio))
                # 偏向匹配历史大小比
                if n > mid:
                    w *= (0.5 + big_ratio)
                else:
                    w *= (0.5 + (1 - big_ratio))
                weights.append(w)

            total_w = sum(weights)
            probs = [w / total_w for w in weights]

            selected_idx = []
            remaining = list(range(len(all_raw)))
            rem_probs = list(probs)
            for _ in range(zone["count"]):
                if not remaining:
                    break
                rp_sum = sum(rem_probs)
                rp_norm = [p / rp_sum for p in rem_probs]
                idx = random.choices(range(len(remaining)), weights=rp_norm, k=1)[0]
                selected_idx.append(all_raw[remaining[idx]])
                remaining.pop(idx)
                rem_probs.pop(idx)

            picks.extend(sorted([fmt(x) for x in selected_idx], key=lambda x: int(x)))
        return picks

    elif "positions" in params:
        if len(history) < 2:
            # 数据不足，退化为加权随机
            return _strategy_weighted(_decayed_counter(history, lottery_key), lottery_key, params)
        # 数字彩：分析相邻位置转移概率（简易马尔可夫）
        n_pos = params["positions"]
        picks = []
        # 取最近一期作为种子
        last_draw = history[0].get("numbers", []) if history else []
        for p in range(n_pos):
            # 统计该位置：上一期出现X时，本期出现Y的频率
            transitions = defaultdict(Counter)
            for i in range(1, len(history)):
                prev = history[i].get("numbers", [])
                curr = history[i - 1].get("numbers", [])
                if p < len(prev) and p < len(curr):
                    transitions[prev[p]][curr[p]] += 1

            seed = last_draw[p] if p < len(last_draw) else str(random.randint(0, 9))
            trans = transitions.get(seed, {})
            if trans:
                items = list(trans.items())
                cands = [n for n, _ in items]
                wgts = [c + 0.5 for _, c in items]
                picks.append(random.choices(cands, weights=wgts, k=1)[0])
            else:
                picks.append(str(random.randint(0, 9)))
        return picks
    return []


# ============================================================
# 3. 回测验证
# ============================================================
def _hit_count(predicted: list, actual: list, lottery_key: str, params: dict) -> int:
    """计算一组预测命中了几个实际号码"""
    if not predicted or not actual:
        return 0
    if "zones" in params:
        return len(set(predicted) & set(actual))
    elif "positions" in params:
        hits = 0
        for i in range(min(len(predicted), len(actual))):
            if predicted[i] == actual[i]:
                hits += 1
        return hits
    return 0


def _backtest_strategy(history: list, lottery_key: str, strategy_fn, trials: int = 3) -> dict:
    """回测：用历史数据验证策略命中率"""
    params = LOTTERY_PARAMS.get(lottery_key)
    if not params or len(history) < 10:
        return {"name": strategy_fn.__name__.replace("_strategy_", ""), "total_trials": 0, "total_hits": 0, "avg_hits": 0.0, "hit_rate": 0.0}

    # 留出最近 holdout 期作为测试
    holdout = min(10, len(history) // 3)
    train = history[holdout:]  # 较早的数据做训练
    test = history[:holdout]   # 最近的数据做验证

    total_hits = 0
    total_trials = 0
    max_possible = 0

    for test_draw in test:
        decayed = _decayed_counter(train, lottery_key)
        max_hits = 0
        for _ in range(trials):
            try:
                pred = strategy_fn(decayed, lottery_key, params, train)
            except TypeError:
                pred = strategy_fn(decayed, lottery_key, params)
            if not pred:
                continue
            hits = _hit_count(pred, test_draw.get("numbers", []), lottery_key, params)
            if hits > max_hits:
                max_hits = hits
        total_hits += max_hits
        total_trials += 1

        if "zones" in params:
            max_possible += sum(z["count"] for z in params["zones"])
        elif "positions" in params:
            max_possible += params["positions"]

        # 滑动窗口：把当前测试期加入训练
        train = [test_draw] + train[:]

    avg_hits = total_hits / max(total_trials, 1)
    hit_rate = total_hits / max(max_possible, 1) * 100

    return {
        "name": strategy_fn.__name__.replace("_strategy_", ""),
        "total_trials": total_trials,
        "total_hits": total_hits,
        "avg_hits": round(avg_hits, 2),
        "hit_rate": round(hit_rate, 1),
    }


def run_backtests(history: list, lottery_key: str) -> list:
    """运行所有策略的回测"""
    strategies = [
        _strategy_hot,
        _strategy_cold,
        _strategy_weighted,
        _strategy_missing,
        _strategy_pattern,
    ]
    results = []
    for fn in strategies:
        try:
            r = _backtest_strategy(history, lottery_key, fn, trials=3)
            results.append(r)
        except Exception as e:
            results.append({"name": fn.__name__, "error": str(e)})

    # 按命中率排序
    results.sort(key=lambda x: x.get("avg_hits", 0), reverse=True)
    return results


# ============================================================
# 4. 自适应预测生成
# ============================================================
def generate_prediction(history: list, lottery_key: str, count: int = 5) -> list:
    """生成推荐号码：回测最优 + 学习权重融合"""
    params = LOTTERY_PARAMS.get(lottery_key)
    if not params:
        return []

    decayed = _decayed_counter(history, lottery_key)

    # 回测选出最佳策略
    backtests = run_backtests(history, lottery_key)
    best_strategy_name = backtests[0]["name"] if backtests else "weighted"

    # 策略映射
    strategy_map = {
        "hot": _strategy_hot,
        "cold": _strategy_cold,
        "weighted": _strategy_weighted,
        "missing": _strategy_missing,
        "pattern": _strategy_pattern,
    }

    # 融合：学习权重 + 回测最佳策略
    weights = LEARNED_WEIGHTS.get(lottery_key, {"hot": 2, "cold": 2, "weighted": 3, "missing": 2, "pattern": 2})
    # 回测最佳策略额外加分
    if best_strategy_name in weights:
        weights[best_strategy_name] = weights.get(best_strategy_name, 1) + 3
    
    total_w = sum(weights.values())
    
    results = []
    for _ in range(count):
        # 按权重随机选择策略
        strategy_names = list(weights.keys())
        strategy_probs = [weights[s] / total_w for s in strategy_names]
        chosen = random.choices(strategy_names, weights=strategy_probs, k=1)[0]
        fn = strategy_map.get(chosen, _strategy_weighted)
        
        try:
            pred = fn(decayed, lottery_key, params, history)
        except TypeError:
            pred = fn(decayed, lottery_key, params)
        if pred:
            results.append(pred)

    return results


# ============================================================
# 5. 频率分析（带衰减）
# ============================================================
def analyze_frequency(history: list, lottery_key: str) -> dict:
    """分析号码出现频率（时间衰减版）"""
    params = LOTTERY_PARAMS.get(lottery_key)
    if not params or not history:
        return {}

    result = {}

    if "zones" in params:
        for zi, zone in enumerate(params["zones"]):
            zone_name = zone["name"]
            start_idx = sum(z["count"] for z in params["zones"][:zi])
            end_idx = start_idx + zone["count"]
            fmt = lambda n: str(n).zfill(2) if zone["range"][1] >= 10 else str(n)

            # 时间衰减计数
            decayed = defaultdict(float)
            total_w = 0.0
            for i, draw in enumerate(history):
                periods_ago = len(history) - 1 - i
                w = _decay_weight(periods_ago)
                total_w += w
                nums = draw.get("numbers", [])
                if len(nums) > end_idx:
                    for n in nums[start_idx:end_idx]:
                        decayed[n] += w

            # 遗漏计算
            all_nums = set(fmt(n) for n in range(zone["range"][0], zone["range"][1] + 1))
            missing = {n: len(history) for n in all_nums}
            for i, draw in enumerate(history):
                nums = draw.get("numbers", [])
                if len(nums) > end_idx:
                    for n in nums[start_idx:end_idx]:
                        n_str = fmt(n)
                        periods = len(history) - 1 - i
                        if periods < missing.get(n_str, 999):
                            missing[n_str] = periods

            # 原始频率（用于对比）
            raw_counter = Counter()
            for draw in history:
                nums = draw.get("numbers", [])
                if len(nums) > end_idx:
                    for n in nums[start_idx:end_idx]:
                        raw_counter[n] += 1

            sorted_freq = sorted(decayed.items(), key=lambda x: -x[1])
            sorted_raw = sorted(raw_counter.items(), key=lambda x: -x[1])
            sorted_missing = sorted(missing.items(), key=lambda x: -x[1])

            result[zone_name] = {
                "hot": [{"num": fmt(n), "freq": round(v, 2), "weight": round(v / max(total_w, 0.001), 3)}
                        for n, v in sorted_freq[:zone["count"] * 2]],
                "cold": [{"num": fmt(n), "freq": round(v, 2), "weight": round(v / max(total_w, 0.001), 3)}
                         for n, v in sorted_freq[-zone["count"] * 2:]],
                "missing": [{"num": n, "periods": p}
                            for n, p in sorted_missing[:zone["count"] * 3] if p > 0],
                "all_freq": [{"num": fmt(n), "freq": round(v, 2), "weight": round(v / max(total_w, 0.001), 3)}
                             for n, v in sorted_freq],
                "raw_freq": [{"num": fmt(n), "freq": c} for n, c in sorted_raw],
            }

    elif "positions" in params:
        n_positions = params["positions"]
        lo, hi = params["range_per_pos"]

        # 每位衰减频率
        pos_decayed = [defaultdict(float) for _ in range(n_positions)]
        pos_total_w = [0.0] * n_positions
        pos_raw = [Counter() for _ in range(n_positions)]
        pos_missing = [{str(i): len(history) for i in range(lo, hi + 1)} for _ in range(n_positions)]

        for i, draw in enumerate(history):
            periods_ago = len(history) - 1 - i
            w = _decay_weight(periods_ago)
            nums = draw.get("numbers", [])
            for p in range(min(n_positions, len(nums))):
                n = nums[p]
                pos_decayed[p][n] += w
                pos_total_w[p] += w
                pos_raw[p][n] += 1
                if pos_missing[p].get(n, 999) > len(history) - 1 - i:
                    pos_missing[p][n] = len(history) - 1 - i

        result["positions"] = []
        for p in range(n_positions):
            tw = max(pos_total_w[p], 0.001)
            sorted_freq = sorted(pos_decayed[p].items(), key=lambda x: -x[1])
            sorted_missing = sorted(pos_missing[p].items(), key=lambda x: -x[1])

            result["positions"].append({
                "pos": p + 1,
                "hot": [{"num": n, "freq": round(v, 2), "weight": round(v / tw, 3)}
                        for n, v in sorted_freq[:3]],
                "cold": [{"num": n, "freq": round(v, 2), "weight": round(v / tw, 3)}
                         for n, v in sorted_freq[-3:]],
                "missing": [{"num": n, "periods": p}
                            for n, p in sorted_missing[:5] if p > 0],
            })

        # 整体频率
        overall = Counter()
        for draw in history:
            for n in draw.get("numbers", []):
                overall[n] += 1
        sorted_overall = sorted(overall.items(), key=lambda x: -x[1])
        result["overall"] = {
            "hot": [{"num": n, "freq": f} for n, f in sorted_overall[:5]],
            "cold": [{"num": n, "freq": f} for n, f in sorted_overall[-5:]],
        }

    return result


# ============================================================
# 6. 模式统计
# ============================================================
def analyze_patterns(history: list, lottery_key: str) -> dict:
    """分析开奖模式：奇偶比、大小比、和值、连号、重号"""
    params = LOTTERY_PARAMS.get(lottery_key)
    if not params or not history:
        return {}

    result = {}

    if "zones" in params:
        for zi, zone in enumerate(params["zones"]):
            zn = zone["name"]
            start_idx = sum(z["count"] for z in params["zones"][:zi])
            end_idx = start_idx + zone["count"]
            lo, hi = zone["range"]
            mid = (lo + hi) // 2

            odd_even_counts = Counter()
            big_small_counts = Counter()
            sums = []
            consecutive_runs = Counter()

            for draw in history:
                nums = draw.get("numbers", [])
                if len(nums) > end_idx:
                    zone_nums = [int(n) for n in nums[start_idx:end_idx]]
                    odd = sum(1 for n in zone_nums if n % 2 == 1)
                    odd_even_counts[f"{odd}奇{zone['count']-odd}偶"] += 1
                    big = sum(1 for n in zone_nums if n > mid)
                    big_small_counts[f"{big}大{zone['count']-big}小"] += 1
                    sums.append(sum(zone_nums))
                    # 连号
                    sorted_nums = sorted(zone_nums)
                    consec = sum(1 for i in range(1, len(sorted_nums)) if sorted_nums[i] - sorted_nums[i-1] == 1)
                    consecutive_runs[consec] += 1

            result[zn] = {
                "odd_even": [{"pattern": k, "count": v} for k, v in odd_even_counts.most_common(5)],
                "big_small": [{"pattern": k, "count": v} for k, v in big_small_counts.most_common(5)],
                "sum_range": {
                    "min": min(sums) if sums else 0,
                    "max": max(sums) if sums else 0,
                    "avg": round(sum(sums) / len(sums), 1) if sums else 0,
                    "recent": sums[-5:] if len(sums) >= 5 else sums,
                },
                "consecutive": [{"pairs": k, "count": v} for k, v in consecutive_runs.most_common(4)],
            }

    elif "positions" in params:
        n_pos = params["positions"]
        # 每位奇偶、大小统计
        for p in range(n_pos):
            odd_count = 0
            big_count = 0
            total = len(history)
            for draw in history:
                nums = draw.get("numbers", [])
                if p < len(nums):
                    n = int(nums[p])
                    if n % 2 == 1:
                        odd_count += 1
                    if n >= 5:
                        big_count += 1

            result[f"pos_{p+1}"] = {
                "odd_ratio": round(odd_count / max(total, 1) * 100, 1),
                "big_ratio": round(big_count / max(total, 1) * 100, 1),
            }

    return result


# ============================================================
# 7. 完整分析
# ============================================================
def full_analysis(lottery_key: str, history: list) -> dict:
    """完整分析：频率 + 模式 + 回测 + 预测"""
    freq = analyze_frequency(history, lottery_key)
    patterns = analyze_patterns(history, lottery_key)
    backtests = run_backtests(history, lottery_key)
    predictions = generate_prediction(history, lottery_key, count=5)

    return {
        "lottery_key": lottery_key,
        "name": LOTTERY_PARAMS.get(lottery_key, {}).get("name", ""),
        "history_count": len(history),
        "frequency": freq,
        "patterns": patterns,
        "backtests": backtests,
        "predictions": predictions,
        "best_strategy": backtests[0].get("name", "weighted") if backtests else "weighted",
    }


if __name__ == "__main__":
    from lottery_fetcher import fetch_all
    import json

    data = fetch_all(30)

    for key in ["dlt", "qxc", "pls", "plw"]:
        history = data[key]["data"]
        print(f"\n{'='*60}")
        print(f"  {data[key]['name']} ({len(history)}期)")
        print(f"{'='*60}")
        analysis = full_analysis(key, history)
        print(f"最佳策略: {analysis['best_strategy']}")
        print(f"回测结果:")
        for bt in analysis.get("backtests", []):
            bar = "█" * int(bt.get("avg_hits", 0) * 10)
            print(f"  {bt['name']:12s} | 均命中 {bt['avg_hits']} | 命中率 {bt['hit_rate']}% {bar}")
        print(f"推荐号码:")
        for i, p in enumerate(analysis.get("predictions", [])):
            print(f"  #{i+1}: {' '.join(p)}")
