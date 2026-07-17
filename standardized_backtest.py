# standardized_backtest.py — 大乐透预测回测标准化工具
"""
每次开奖后运行此脚本进行标准化回测。

用法:
  python standardized_backtest.py "01 04 10 23 25" "01 12"
  python standardized_backtest.py --interactive    (交互式输入)

输出:
  1. 控制台：完整回测报告
  2. 桌面：大乐透回测_YYYYMMDD_HHMMSS.txt  (详细报告存档)
  3. lottery_predictor.py：自动更新策略权重

流程:
  ① 加载最新预测文件
  ② 单式回测（命中分布、中奖统计、最佳单注）
  ③ 复式回测（覆盖统计、展开中奖）
  ④ 花费与奖金计算
  ⑤ 策略诊断与学习建议
  ⑥ 自动更新模型权重
"""

import re
import sys
import io
import os
import json
import glob
import math
from datetime import datetime
from collections import Counter, defaultdict
from itertools import combinations

# 修复Windows编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# ============================================================
# 配置
# ============================================================
PROJECT_DIR = r"C:\Users\lenovo1\Documents\足彩"
DESKTOP_DIR = r"C:\Users\lenovo1\Desktop"
PREDICTOR_PATH = os.path.join(PROJECT_DIR, "lottery_predictor.py")
CACHE_PATH = os.path.join(PROJECT_DIR, "data", "lottery_cache", "dlt.json")

# 奖级规则
PRIZE_RULES = {
    (5, 2): ("一等奖", "浮动", 0),
    (5, 1): ("二等奖", "浮动", 0),
    (5, 0): ("三等奖", 10000, 10000),
    (4, 2): ("四等奖", 3000, 3000),
    (4, 1): ("五等奖", 300, 300),
    (3, 2): ("六等奖", 200, 200),
    (4, 0): ("七等奖", 100, 100),
    (3, 1): ("八等奖", 15, 15),
    (2, 2): ("八等奖", 15, 15),
    (3, 0): ("九等奖", 5, 5),
    (1, 2): ("九等奖", 5, 5),
    (2, 1): ("九等奖", 5, 5),
    (0, 2): ("九等奖", 5, 5),
}

STRATEGY_NAMES = ["热号追踪", "冷号反弹", "温冷回补", "综合加权", "遗漏回补", "模式匹配"]

# ============================================================
# 工具函数
# ============================================================
def find_latest_prediction_file():
    """找到最新的预测文件"""
    pattern = os.path.join(DESKTOP_DIR, "大乐透_*组_*.txt")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)


def parse_single_line(line):
    parts = line.strip().split()
    if len(parts) >= 9 and parts[0] == '#':
        try:
            idx = int(parts[1])
            front = set(parts[2:7])
            back = set(parts[7:9])
            return idx, front, back
        except (ValueError, IndexError):
            pass
    return None


def parse_compound_line(line):
    fm = re.search(r'前区\((\d+)码\):(.+?)后区', line)
    bm = re.search(r'后区\((\d+)码\):(.+?)(?:=|$)', line)
    cm = re.search(r'=(\d+)注(\d+)元', line)
    im = re.match(r'#\s+(\d+)', line)
    if fm and bm and cm and im:
        idx = int(im.group(1))
        front = set(fm.group(2).strip().split())
        back = set(bm.group(2).strip().split())
        bets = int(cm.group(1))
        cost = int(cm.group(2))
        return idx, front, back, bets, cost
    return None


def get_prize(fh, bh):
    key = (fh, bh)
    info = PRIZE_RULES.get(key)
    if info:
        return info[0], info[2]
    return ("未中奖", 0)


def safe_div(a, b):
    return a / b if b > 0 else 0


# ============================================================
# 主回测逻辑
# ============================================================
def run_backtest(winning_front, winning_back, pred_file):
    """执行完整回测，返回结构化结果"""
    with open(pred_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()

    # 从文件名提取时间戳
    ts_match = re.search(r'(\d{8}_\d{6})', os.path.basename(pred_file))
    pred_ts = ts_match.group(1) if ts_match else "unknown"

    # 初始化结果存储
    single = {s: {"total": 0, "fh_dist": defaultdict(int), "bh_dist": defaultdict(int),
                   "prizes": defaultdict(int), "prize_count": 0, "prize_amount": 0,
                   "best_fh": 0, "best_bh": 0, "best_idx": 0, "best_line": "",
                   "any_hit": 0} for s in STRATEGY_NAMES}

    compound = {s: {"total": 0, "fh_dist": defaultdict(int), "bh_dist": defaultdict(int),
                     "covers_all": 0, "covers_front": 0, "covers_back": 0,
                     "total_bets": 0, "total_cost": 0,
                     "prizes": defaultdict(int), "prize_amount": 0,
                     "best_fh": 0, "best_bh": 0, "best_idx": 0, "best_line": "",
                    } for s in STRATEGY_NAMES}

    current_strategy = None
    current_mode = None

    for line in lines:
        # 检测策略
        for sn in STRATEGY_NAMES:
            if sn in line:
                current_strategy = sn
                break

        # 检测模式
        if "单选" in line and "1000组" in line:
            current_mode = "single"
            continue
        elif "复式" in line and "1000组" in line:
            current_mode = "compound"
            continue

        if current_strategy is None or current_mode is None:
            continue

        if current_mode == "single":
            parsed = parse_single_line(line)
            if parsed:
                idx, front, back = parsed
                fh = len(front & winning_front)
                bh = len(back & winning_back)
                r = single[current_strategy]
                r["total"] += 1
                r["fh_dist"][fh] += 1
                r["bh_dist"][bh] += 1
                name, amt = get_prize(fh, bh)
                if name != "未中奖":
                    r["prizes"][name] += 1
                    r["prize_count"] += 1
                    r["prize_amount"] += amt
                if fh > 0 or bh > 0:
                    r["any_hit"] += 1
                if fh > r["best_fh"] or (fh == r["best_fh"] and bh > r["best_bh"]):
                    r["best_fh"], r["best_bh"], r["best_idx"], r["best_line"] = fh, bh, idx, line.strip()

        elif current_mode == "compound":
            parsed = parse_compound_line(line)
            if parsed:
                idx, front, back, bets, cost = parsed
                fh = len(front & winning_front)
                bh = len(back & winning_back)
                r = compound[current_strategy]
                r["total"] += 1
                r["total_bets"] += bets
                r["total_cost"] += cost
                r["fh_dist"][fh] += 1
                r["bh_dist"][bh] += 1
                if winning_front.issubset(front) and winning_back.issubset(back):
                    r["covers_all"] += 1
                if winning_front.issubset(front):
                    r["covers_front"] += 1
                if winning_back.issubset(back):
                    r["covers_back"] += 1
                if fh > r["best_fh"] or (fh == r["best_fh"] and bh > r["best_bh"]):
                    r["best_fh"], r["best_bh"], r["best_idx"], r["best_line"] = fh, bh, idx, line.strip()

                # 枚举复式所有组合计算中奖
                front_list = sorted(front, key=int)
                back_list = sorted(back, key=int)
                for fc in combinations(front_list, 5):
                    for bc in combinations(back_list, 2):
                        cfh = len(set(fc) & winning_front)
                        cbh = len(set(bc) & winning_back)
                        cname, camt = get_prize(cfh, cbh)
                        if cname != "未中奖":
                            r["prizes"][cname] += 1
                            r["prize_amount"] += camt

    return single, compound, pred_ts


# ============================================================
# 报告生成
# ============================================================
def generate_report(winning_front, winning_back, single, compound, pred_ts, pred_file):
    """生成完整回测报告"""
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    report_ts = datetime.now().strftime("%Y%m%d_%H%M%S")

    lines = []
    def w(s=""):
        lines.append(s)

    w("=" * 78)
    w("  大乐透预测回测报告")
    w(f"  回测时间: {now}")
    w(f"  开奖号码: 前区 {' '.join(sorted(winning_front, key=int))}  后区 {' '.join(sorted(winning_back, key=int))}")
    w(f"  预测文件: {os.path.basename(pred_file)}")
    w("=" * 78)

    # ── 一、单式回测 ──
    w()
    w("─" * 78)
    w("  【一、单式回测】每策略1000注，共6000注")
    w("─" * 78)

    for s in STRATEGY_NAMES:
        r = single[s]
        t = r["total"]
        w()
        w(f"  ▸ {s}  (总{t}注)")
        # 前区分布
        parts = [f"{k}码:{r['fh_dist'].get(k,0)}注({safe_div(r['fh_dist'].get(k,0),t)*100:.1f}%)"
                 for k in range(6) if r['fh_dist'].get(k, 0) > 0]
        w(f"    前区: {', '.join(parts)}")
        # 后区分布
        parts = [f"{k}码:{r['bh_dist'].get(k,0)}注({safe_div(r['bh_dist'].get(k,0),t)*100:.1f}%)"
                 for k in range(3) if r['bh_dist'].get(k, 0) > 0]
        w(f"    后区: {', '.join(parts)}")
        # 中奖
        w(f"    中奖: {r['prize_count']}注 ({safe_div(r['prize_count'],t)*100:.2f}%), 奖金 {r['prize_amount']:,}元")
        if r['prizes']:
            detail = ', '.join(f"{p}{r['prizes'][p]}注" for p in ["一等奖","二等奖","三等奖","四等奖","五等奖","六等奖","七等奖","八等奖","九等奖"] if p in r['prizes'])
            w(f"           {detail}")
        # 至少中1个
        w(f"    至少中1号: {r['any_hit']}注 ({safe_div(r['any_hit'],t)*100:.1f}%)")
        # 最佳
        pname, pamt = get_prize(r['best_fh'], r['best_bh'])
        w(f"    最佳: #{r['best_idx']} 前{r['best_fh']}+后{r['best_bh']} → {pname}")
        w(f"          {r['best_line'][:90]}")

    # ── 二、复式回测 ──
    w()
    w("─" * 78)
    w("  【二、复式回测】每策略1000注，共6000注")
    w("─" * 78)

    for s in STRATEGY_NAMES:
        r = compound[s]
        t = r["total"]
        w()
        w(f"  ▸ {s}  (总{t}注, {r['total_bets']:,}投注, {r['total_cost']:,}元)")
        w(f"    全覆盖5+2: {r['covers_all']}注 ({safe_div(r['covers_all'],t)*100:.2f}%)")
        w(f"    覆盖全部前区: {r['covers_front']}注, 覆盖全部后区: {r['covers_back']}注")
        # 前区分布
        parts = [f"{k}码:{r['fh_dist'].get(k,0)}注" for k in sorted(r['fh_dist'].keys())]
        w(f"    前区: {', '.join(parts)}")
        parts = [f"{k}码:{r['bh_dist'].get(k,0)}注" for k in sorted(r['bh_dist'].keys())]
        w(f"    后区: {', '.join(parts)}")
        # 中奖
        w(f"    展开中奖: {r['prize_amount']:,}元")
        if r['prizes']:
            detail = ', '.join(f"{p}{r['prizes'][p]}注" for p in ["一等奖","二等奖","三等奖","四等奖","五等奖","六等奖","七等奖","八等奖","九等奖"] if p in r['prizes'])
            w(f"             {detail}")
        # 最佳
        w(f"    最佳: #{r['best_idx']} 命中前{r['best_fh']}+后{r['best_bh']}")
        w(f"          {r['best_line'][:100]}...")

    # ── 三、费用与奖金 ──
    w()
    w("─" * 78)
    w("  【三、费用与奖金汇总】")
    w("─" * 78)

    total_single_cost = sum(r["total"] * 2 for r in single.values())
    total_single_prize = sum(r["prize_amount"] for r in single.values())
    total_compound_cost = sum(r["total_cost"] for r in compound.values())
    total_compound_prize = sum(r["prize_amount"] for r in compound.values())
    total_cost = total_single_cost + total_compound_cost
    total_prize = total_single_prize + total_compound_prize

    w()
    w(f"  单式: {sum(r['total'] for r in single.values())}注 × 2元 = {total_single_cost:,}元  →  中奖 {total_single_prize:,}元  (回报率 {safe_div(total_single_prize,total_single_cost)*100:.1f}%)")
    w(f"  复式: {sum(r['total_bets'] for r in compound.values()):,}投注 = {total_compound_cost:,}元  →  中奖 {total_compound_prize:,}元  (回报率 {safe_div(total_compound_prize,total_compound_cost)*100:.1f}%)")
    w(f"  {'─'*50}")
    w(f"  合计花费: {total_cost:,}元")
    w(f"  合计中奖: {total_prize:,}元")
    w(f"  净盈亏:   {total_prize - total_cost:,}元")
    w(f"  总回报率: {safe_div(total_prize,total_cost)*100:.1f}%")

    # ── 四、策略排名 ──
    w()
    w("─" * 78)
    w("  【四、策略排名】")
    w("─" * 78)
    w()
    # 单式中奖率排名
    ranked = sorted(STRATEGY_NAMES, key=lambda s: single[s]["prize_amount"], reverse=True)
    w(f"  {'单式中奖金额排名':-^50}")
    w(f"  {'排名':<6}{'策略':<12}{'中奖注':<8}{'中奖金额':<10}{'中奖率':<8}{'最佳命中'}")
    for i, s in enumerate(ranked):
        r = single[s]
        w(f"  {i+1:<6}{s:<12}{r['prize_count']:<8}{r['prize_amount']:<10,}{safe_div(r['prize_count'],r['total'])*100:<8.2f}%前{r['best_fh']}+后{r['best_bh']}")

    w()
    ranked_c = sorted(STRATEGY_NAMES, key=lambda s: compound[s]["prize_amount"], reverse=True)
    w(f"  {'复式展开中奖金额排名':-^50}")
    for i, s in enumerate(ranked_c):
        r = compound[s]
        w(f"  {i+1:<6}{s:<12}{r['prize_amount']:<15,}全覆盖{r['covers_all']}注 前区覆盖{r['covers_front']}注")

    # ── 五、学习建议 ──
    w()
    w("─" * 78)
    w("  【五、学习诊断】")
    w("─" * 78)

    best_single = ranked[0]
    worst_single = ranked[-1]
    w()
    w(f"  ✓ 最佳单式策略: {best_single} (中奖 {single[best_single]['prize_amount']:,}元)")
    w(f"  ✗ 最差单式策略: {worst_single} (中奖 {single[worst_single]['prize_amount']:,}元)")

    # 分析中奖号码特征
    w()
    w("  中奖号码特征:")

    # 尝试加载历史数据做深入分析
    try:
        with open(CACHE_PATH, 'r', encoding='utf-8') as f:
            hist_data = json.load(f)
        history = hist_data.get('results', [])

        # 分析每个中奖号码
        for n_str in sorted(winning_front, key=int):
            global_count = sum(1 for d in history if n_str in d.get('numbers',[])[:5])
            last_seen = next((i for i, d in enumerate(history) if n_str in d.get('numbers',[])[:5]), len(history))
            global_rate = safe_div(global_count, len(history)) * 100
            tag = "热" if global_rate >= 16 else ("冷" if global_rate <= 12 else "温")
            tag2 = "近期热" if last_seen <= 1 else (f"{last_seen}期未出" if last_seen >= 5 else "近期温")
            w(f"    前区{n_str}: 全局{global_rate:.0f}%({tag}) | {tag2}")

        for n_str in sorted(winning_back, key=int):
            global_count = sum(1 for d in history if n_str in d.get('numbers',[])[5:7])
            last_seen = next((i for i, d in enumerate(history) if n_str in d.get('numbers',[])[5:7]), len(history))
            global_rate = safe_div(global_count, len(history)) * 100
            tag = "热" if global_rate >= 18 else ("冷" if global_rate <= 13 else "温")
            tag2 = "近期热" if last_seen <= 1 else (f"{last_seen}期未出" if last_seen >= 6 else "近期温")
            w(f"    后区{n_str}: 全局{global_rate:.0f}%({tag}) | {tag2}")
    except Exception as e:
        w(f"    (无法加载历史数据: {e})")

    # 建议
    w()
    w("  改进建议:")
    suggestions = []

    # 检查温冷回补是否覆盖了关键号码
    for n_str in winning_front:
        if 5 <= next((i for i, d in enumerate(history) if n_str in d.get('numbers',[])[:5]), 0) <= 15:
            suggestions.append(f"前区{n_str}处于甜蜜点区间(5-15期)，确认温冷回补策略已覆盖")
    for n_str in winning_back:
        if 5 <= next((i for i, d in enumerate(history) if n_str in d.get('numbers',[])[5:7]), 0) <= 15:
            suggestions.append(f"后区{n_str}处于甜蜜点区间(5-15期)，确认温冷回补策略已覆盖")

    # 如果某些策略特别差
    if single[worst_single]["prize_count"] == 0:
        suggestions.append(f"'{worst_single}'策略本期零中奖，考虑降低其权重或检查策略逻辑")

    # 冷热判断
    hot_count = sum(1 for n_str in winning_front if safe_div(sum(1 for d in history if n_str in d.get('numbers',[])[:5]), len(history)) * 100 >= 16)
    cold_count = sum(1 for n_str in winning_front if safe_div(sum(1 for d in history if n_str in d.get('numbers',[])[:5]), len(history)) * 100 <= 12)
    suggestions.append(f"本期前区: {hot_count}热 + {cold_count}冷 + {5-hot_count-cold_count}温 = {'偏热' if hot_count >= 3 else ('偏冷' if cold_count >= 3 else '冷热均衡')}")

    for sug in suggestions:
        w(f"    • {sug}")

    # ── 六、权重更新建议 ──
    w()
    w("─" * 78)
    w("  【六、自动权重更新】")
    w("─" * 78)
    w()

    # 基于本期表现计算新权重
    # 按照中奖金额排序分配权重
    total_prize_all = sum(single[s]["prize_amount"] for s in STRATEGY_NAMES)
    new_weights = {}
    if total_prize_all > 0:
        for s in STRATEGY_NAMES:
            # 权重 = 基础分 + 表现加分
            base = 1
            performance = int(single[s]["prize_amount"] / max(total_prize_all / 6, 1) * 3)
            new_weights[s] = min(max(base + performance, 1), 8)
    else:
        for s in STRATEGY_NAMES:
            new_weights[s] = 1

    w(f"  建议新权重 (基于本期表现):")
    w(f"  {'策略':<12} {'旧权重':>6} {'建议新权重':>8}")
    w(f"  {'─'*30}")

    # 读取当前权重
    try:
        with open(PREDICTOR_PATH, 'r', encoding='utf-8') as f:
            predictor_code = f.read()
        import re as _re
        dlt_match = _re.search(r'"dlt"\s*:\s*\{([^}]+)\}', predictor_code)
        old_weights = {}
        if dlt_match:
            for item in dlt_match.group(1).split(','):
                kv = item.strip().split(':')
                if len(kv) == 2:
                    old_weights[kv[0].strip().strip('"')] = int(kv[1].strip())
    except:
        old_weights = {}

    weight_map = {
        "热号追踪": "hot", "冷号反弹": "cold", "温冷回补": "warm_cold",
        "综合加权": "weighted", "遗漏回补": "missing", "模式匹配": "pattern"
    }

    for s in STRATEGY_NAMES:
        key = weight_map.get(s, s)
        old = old_weights.get(key, '?')
        new = new_weights.get(s, '?')
        marker = ""
        if new != old and old != '?':
            marker = f"  ← {'↑' if new > old else '↓'}"
        w(f"  {s:<12} {str(old):>6} {str(new):>8}{marker}")

    # 自动应用更新
    w()
    w("  正在自动应用权重更新...")
    try:
        # 构建新权重字符串
        new_dlt_weights = "{" + ", ".join(
            f'"{weight_map.get(s,s)}": {new_weights[s]}' for s in STRATEGY_NAMES
        ) + "}"
        old_dlt = _re.search(r'"dlt"\s*:\s*\{[^}]+\}', predictor_code).group(0)
        new_code = predictor_code.replace(old_dlt, f'"dlt":  {new_dlt_weights}')

        # 备份
        backup = PREDICTOR_PATH.replace('.py', f'_backup_{report_ts}.py')
        with open(backup, 'w', encoding='utf-8') as f:
            f.write(predictor_code)

        # 更新
        with open(PREDICTOR_PATH, 'w', encoding='utf-8') as f:
            f.write(new_code)
        w(f"  ✓ 权重已更新! 备份: {os.path.basename(backup)}")
    except Exception as e:
        w(f"  ✗ 自动更新失败: {e}")
        w(f"  请手动编辑 {PREDICTOR_PATH} 中的 LEARNED_WEIGHTS")

    w()
    w("=" * 78)
    w(f"  回测完成 — {now}")
    w("=" * 78)

    # 保存到文件
    report_text = "\n".join(lines)
    report_path = os.path.join(DESKTOP_DIR, f"大乐透回测_{report_ts}.txt")
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report_text)

    return report_text, report_path


# ============================================================
# 入口
# ============================================================
def main():
    print("大乐透预测回测工具 v2.0")
    print("=" * 50)

    # 交互式输入
    use_interactive = "--interactive" in sys.argv or "-i" in sys.argv

    if use_interactive:
        print()
        front_input = input("请输入前区5个中奖号码 (空格分隔，如 01 04 10 23 25): ").strip()
        back_input = input("请输入后区2个中奖号码 (空格分隔，如 01 12): ").strip()
    elif len(sys.argv) >= 3:
        front_input = sys.argv[1]
        back_input = sys.argv[2]
    else:
        print()
        print("用法:")
        print('  python standardized_backtest.py "01 04 10 23 25" "01 12"')
        print("  python standardized_backtest.py --interactive")
        sys.exit(1)

    # 解析号码
    winning_front = set(f"{int(n):02d}" for n in front_input.split())
    winning_back = set(f"{int(n):02d}" for n in back_input.split())

    if len(winning_front) != 5 or len(winning_back) != 2:
        print(f"错误: 前区需要5个号码，后区需要2个号码")
        print(f"  前区: {sorted(winning_front)} ({len(winning_front)}个)")
        print(f"  后区: {sorted(winning_back)} ({len(winning_back)}个)")
        sys.exit(1)

    print(f"\n开奖号码: 前区 {' '.join(sorted(winning_front))}  后区 {' '.join(sorted(winning_back))}")

    # 找预测文件
    pred_file = find_latest_prediction_file()
    if not pred_file:
        print("错误: 未找到预测文件! 请先生成预测。")
        print(f"  搜索路径: {DESKTOP_DIR}\\大乐透_*组_*.txt")
        sys.exit(1)

    print(f"预测文件: {os.path.basename(pred_file)}")
    print()

    # 运行回测
    print("正在回测...")
    single, compound, pred_ts = run_backtest(winning_front, winning_back, pred_file)

    # 生成报告
    print("正在生成报告...")
    report_text, report_path = generate_report(winning_front, winning_back, single, compound, pred_ts, pred_file)

    # 输出到控制台
    print(report_text)
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
