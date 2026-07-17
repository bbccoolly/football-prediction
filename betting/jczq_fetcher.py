# betting/jczq_fetcher.py — 500.com 竞彩足球赔率抓取器
"""从 trade.500.com 抓取多玩法赔率数据，解析为结构化比赛信息。
支持: SPF(269) / RQSPF(312) / 总进球(270) / 比分(271) / 半全场(272)
"""

import re
import json
import time
import os
import requests
from datetime import datetime

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

# 玩法ID映射
PLAY_TYPES = {
    "spf": 269,      # 胜平负
    "rqspf": 312,    # 让球胜平负
    "goals": 270,    # 总进球
    "score": 271,    # 比分
    "htft": 272,     # 半全场
}

BASE_URL = "https://trade.500.com/jczq/"

# 缓存目录
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "raw")
CACHE_FILE = os.path.join(CACHE_DIR, "jczq_odds_cache.json")


def _fetch_html(play_id: int = 269) -> str:
    """抓取单个玩法的HTML"""
    url = f"{BASE_URL}?playid={play_id}"
    for scheme in ["https", "http"]:
        try:
            full_url = url if scheme == "https" else url.replace("https://", "http://")
            resp = requests.get(full_url, headers=HEADERS, timeout=15)
            resp.encoding = "gb2312"
            html = resp.text
            if len(html) > 3000:
                return html
        except Exception as e:
            print(f"  [fetch] {scheme}:// {play_id}: {e}")
    return ""


def _parse_match_row(row: str) -> dict:
    """解析单场比赛行"""
    result = {}

    # 赛事编号 (如 周001, 周日025)
    mid = re.search(r"(周[一二三四五六日]\d+)", row)
    result["id"] = mid.group(1) if mid else ""

    # 是否单关
    result["single_bet"] = "单关" in row

    # 开赛时间
    time_m = re.search(r"(\d{2}-\d{2}\s+\d{2}:\d{2})", row)
    result["time"] = time_m.group(1) if time_m else ""

    # 联赛名称和球队名称 - 从 <a> 标签提取
    anchors = re.findall(r"<a[^>]*?>([^<]+)</a>", row)
    # anchors 通常: [联赛, 主队, 客队, ...]
    if len(anchors) >= 3:
        result["league"] = anchors[0].strip()
        result["home"] = anchors[1].strip()
        result["away"] = anchors[2].strip()
    else:
        result["league"] = ""
        result["home"] = ""
        result["away"] = ""

    # 让球数
    hcp = re.search(r"([+-]\d)", row)
    result["handicap"] = int(hcp.group(1)) if hcp else 0

    # 所有赔率数字
    odds_nums = re.findall(r">(\d+\.\d{2})<", row)
    result["all_odds"] = [float(o) for o in odds_nums]

    # SPF 赔率 = 前3个 (如果是SPF页面)
    if len(odds_nums) >= 3:
        result["spf_odds"] = [float(odds_nums[0]), float(odds_nums[1]), float(odds_nums[2])]

    # RQSPF 赔率 = 第4-6个 (如果是RQSPF页面)
    if len(odds_nums) >= 6:
        result["rqspf_odds"] = [float(odds_nums[3]), float(odds_nums[4]), float(odds_nums[5])]

    # 总进球赔率 = 8个值 (如果是总进球页面)
    if len(odds_nums) >= 8:
        result["goals_odds"] = [float(o) for o in odds_nums[:8]]

    return result


def _parse_matches(html: str) -> list:
    """从HTML中解析所有比赛"""
    # 找所有 bet-tb-tr 行
    rows = re.findall(r'<tr[^>]*bet-tb-tr[^>]*>(.*?)</tr>', html, re.DOTALL)
    matches = []
    for row in rows:
        m = _parse_match_row(row)
        if m.get("home") and m.get("away") and m.get("id"):
            # 过滤无效行（联赛名等）
            skip = ["退出", "个人中心", "全选", "反选", "保存方案", "返回修改",
                    "设为首页", "网站地图", "我的彩票", "登录", "注册", "开奖",
                    "首页", "竞彩足球", "北京单场", "胜负彩", "任选九"]
            if any(w == m["home"] or w == m["away"] for w in skip):
                continue
            if len(m["home"]) > 30 or len(m["away"]) > 30:
                continue
            matches.append(m)
    return matches


def fetch_all_odds(force_refresh: bool = False, use_cache: bool = True) -> dict:
    """抓取所有玩法的赔率数据，合并为统一结构
    
    Returns:
        {
            "matches": [
                {
                    "id": "周日001",
                    "league": "英超",
                    "time": "06-18 22:00",
                    "home": "曼联",
                    "away": "利物浦",
                    "single_bet": True,  # 全场胜平负可单关
                    "single_bet_rq": False,  # 让球可单关
                    "single_bet_goals": False,
                    "handicap": -1,
                    "odds": {
                        "spf": [1.50, 3.80, 5.50],
                        "rqspf": [2.60, 3.30, 2.30],
                        "goals": [10.0, 4.50, 3.20, 3.50, 5.80, 12.0, 24.0, 35.0]
                    }
                },
                ...
            ],
            "fetched_at": "2026-06-18T18:00:00"
        }
    """
    os.makedirs(CACHE_DIR, exist_ok=True)

    # 尝试读缓存 (15分钟内有效)
    if use_cache and not force_refresh and os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        if (time.time() - mtime) < 900:  # 15分钟
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if data.get("matches"):
                print(f"[缓存] {len(data['matches'])} 场比赛")
                return data

    print("[抓取] 正在获取 500.com 赔率...")

    # 并行抓取 SPF 和 RQSPF
    html_spf = _fetch_html(PLAY_TYPES["spf"])
    time.sleep(1.5)
    html_rqspf = _fetch_html(PLAY_TYPES["rqspf"])
    time.sleep(1.5)
    html_goals = _fetch_html(PLAY_TYPES["goals"])

    # 解析
    spf_matches = _parse_matches(html_spf) if html_spf else []
    rqspf_matches = _parse_matches(html_rqspf) if html_rqspf else []
    goals_matches = _parse_matches(html_goals) if html_goals else []

    print(f"  SPF: {len(spf_matches)} 场, RQSPF: {len(rqspf_matches)} 场, 总进球: {len(goals_matches)} 场")

    # 索引化
    rq_by_id = {m["id"]: m for m in rqspf_matches}
    goals_by_id = {m["id"]: m for m in goals_matches}

    # 合并
    merged = []
    for m in spf_matches:
        mid = m["id"]
        entry = {
            "id": mid,
            "league": m.get("league", ""),
            "time": m.get("time", ""),
            "home": m.get("home", ""),
            "away": m.get("away", ""),
            "single_bet": m.get("single_bet", False),
            "single_bet_rq": rq_by_id.get(mid, {}).get("single_bet", False),
            "single_bet_goals": goals_by_id.get(mid, {}).get("single_bet", False),
            "handicap": rq_by_id.get(mid, {}).get("handicap", 0),
            "odds": {
                "spf": m.get("spf_odds", [0, 0, 0]),
                "rqspf": rq_by_id.get(mid, {}).get("rqspf_odds", [0, 0, 0]),
                "goals": goals_by_id.get(mid, {}).get("goals_odds", [0]*8),
            }
        }
        merged.append(entry)

    result = {
        "matches": merged,
        "fetched_at": datetime.now().isoformat(),
        "source": "500.com",
    }

    # 写缓存
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"[抓取] 合并完成: {len(merged)} 场比赛")
    return result


# ---- 示例数据（兜底/测试用） ----
SAMPLE_MATCHES = [
    {
        "id": "周日025", "league": "世界杯", "time": "06-18 22:00",
        "home": "捷克", "away": "南非", "single_bet": True,
        "single_bet_rq": False, "single_bet_goals": False,
        "handicap": -1,
        "odds": {
            "spf": [1.61, 3.50, 4.50],
            "rqspf": [3.00, 3.30, 2.03],
            "goals": [9.60, 4.40, 3.10, 3.50, 6.20, 12.50, 23.0, 34.0]
        }
    },
    {
        "id": "周日026", "league": "世界杯", "time": "06-18 24:00",
        "home": "瑞士", "away": "波黑", "single_bet": False,
        "single_bet_rq": False, "single_bet_goals": False,
        "handicap": -1,
        "odds": {
            "spf": [1.38, 3.95, 6.60],
            "rqspf": [2.37, 3.22, 2.52],
            "goals": [11.5, 4.70, 3.25, 3.30, 5.50, 11.5, 23.0, 32.0]
        }
    },
    {
        "id": "周日027", "league": "世界杯", "time": "06-18 03:00",
        "home": "加拿大", "away": "卡塔尔", "single_bet": True,
        "single_bet_rq": False, "single_bet_goals": False,
        "handicap": -1,
        "odds": {
            "spf": [1.50, 3.80, 5.20],
            "rqspf": [2.50, 3.25, 2.38],
            "goals": [10.0, 4.50, 3.20, 3.40, 6.90, 14.0, 28.0, 38.0]
        }
    },
    {
        "id": "周日028", "league": "世界杯", "time": "06-18 05:00",
        "home": "墨西哥", "away": "韩国", "single_bet": True,
        "single_bet_rq": False, "single_bet_goals": False,
        "handicap": -1,
        "odds": {
            "spf": [2.20, 3.10, 2.85],
            "rqspf": [4.60, 3.75, 1.55],
            "goals": [7.50, 3.80, 3.10, 3.50, 6.25, 13.0, 26.0, 35.0]
        }
    },
]

