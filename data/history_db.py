"""
data/history_db.py -- 持久化历史比赛数据库
"""

import json, os, time
from datetime import datetime

DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "processed", "match_history.json")

def load_history():
    """加载所有历史比赛数据"""
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    if not os.path.exists(DB_FILE):
        return _build_initial_db()
    with open(DB_FILE, "r", encoding="utf-8") as f:
        data = json.load(f)
    matches = data.get("matches", [])
    if len(matches) < 50:
        return _build_initial_db()
    return matches

def _build_initial_db():
    """首次运行：抓取 OpenLigaDB + 500.com 数据构建初始数据库"""
    print("[历史库] 首次构建，抓取真实数据...")
    matches = []

    # 1. OpenLigaDB 德甲 2024 (306场)
    try:
        import requests
        HEADERS = {"User-Agent": "Mozilla/5.0"}
        for md in range(1, 35):
            try:
                r = requests.get(f"https://api.openligadb.de/getmatchdata/bl1/2024/{md}", headers=HEADERS, timeout=10)
                if r.status_code != 200: continue
                for m in r.json():
                    if not m.get("matchResults") or len(m["matchResults"]) < 1: continue
                    res = m["matchResults"][-1]  # last = full-time (index 0 = halftime)
                    if res.get("pointsTeam1") is None: continue
                    matches.append({
                        "home_team": m.get("team1",{}).get("teamName",""),
                        "away_team": m.get("team2",{}).get("teamName",""),
                        "home_goals": int(res["pointsTeam1"]),
                        "away_goals": int(res["pointsTeam2"]),
                        "league": "德甲",
                        "date": m.get("matchDateTime","")[:10],
                    })
                time.sleep(0.1)
            except: pass
        print(f"  [OpenLigaDB] {len(matches)} 场")
    except Exception as e:
        print(f"  [OpenLigaDB] 失败: {e}")

    # 2. 500.com 近期完场
    try:
        import requests, re
        HEADERS = {"User-Agent": "Mozilla/5.0"}
        for d in range(1, 15):
            ds = (datetime.now().strftime("%Y-%m-%d") if d == 0 else 
                  f"2026-06-{13-d:02d}" if d <= 13 else f"2026-05-{31-(d-13):02d}")
            try:
                r = requests.get(f"https://live.500.com/?e={ds}", headers=HEADERS, timeout=10)
                r.encoding = "gb2312"
                skip = ["退出","个人中心","全选","反选","设为首页","首页","开奖","登录","注册","比分","完","直播","待"]
                for row in re.findall(r'<tr[^>]*?>(.*?)</tr>', r.text, re.DOTALL):
                    sm = re.search(r'(\d+)\s*-\s*(\d+)', row)
                    if not sm: continue
                    teams = re.findall(r'<a[^>]*?>([^<]{2,30})</a>', row)
                    if len(teams) < 2: continue
                    h, a = teams[0].strip(), teams[-1].strip()
                    if any(w in h or w in a for w in skip): continue
                    matches.append({"home_team":h,"away_team":a,"home_goals":int(sm.group(1)),"away_goals":int(sm.group(2)),"league":"","date":ds})
                time.sleep(0.3)
            except: pass
        print(f"  [500.com] 总计 {len(matches)} 场")
    except Exception as e:
        print(f"  [500.com] 失败: {e}")

    # 去重
    seen = set()
    unique = []
    for m in matches:
        key = f"{m['home_team']}|{m['away_team']}|{m.get('date','')}"
        if key not in seen:
            seen.add(key)
            unique.append(m)

    print(f"  [历史库] 去重后 {len(unique)} 场")

    # 如果数据太少（网络不行），直接返回空，不生成假数据
    if len(unique) < 30:
        print(f"  [历史库] 数据不足 ({len(unique)}场)，保留真实数据不补充")

    save_history(unique)
    return unique

def _generate_seed_data():
    """最小种子数据 -- 基于真实足球统计的合理模拟"""
    import random
    rng = random.Random(42)
    teams = [
        "拜仁","多特蒙德","莱比锡","勒沃库森","斯图加特","法兰克福","弗赖堡","霍芬海姆",
        "曼城","阿森纳","利物浦","曼联","切尔西","热刺","纽卡斯尔","维拉",
        "皇马","巴萨","马竞","塞维利亚","皇家社会",
        "国米","AC米兰","尤文图斯","那不勒斯","罗马",
        "巴黎","马赛","摩纳哥","里昂","里尔",
    ]
    matches = []
    # 模拟真实比分分布：主场胜~45%, 平局~25%, 客胜~30%
    for _ in range(200):
        h = rng.choice(teams); a = rng.choice(teams)
        if h == a: continue
        rv = rng.random()
        if rv < 0.45:
            hg = rng.choices([1,2,3,4], weights=[30,25,12,5])[0]
            ag = rng.choices([0,1,2], weights=[35,18,5])[0]
            if ag >= hg: ag = hg - 1
        elif rv < 0.70:
            g = rng.choices([0,1,2,3], weights=[12,28,20,5])[0]
            hg = ag = g
        else:
            ag = rng.choices([1,2,3,4], weights=[28,22,10,3])[0]
            hg = rng.choices([0,1,2], weights=[30,15,5])[0]
            if hg >= ag: hg = ag - 1
        hg = max(0, hg); ag = max(0, ag)
        matches.append({"home_team":h,"away_team":a,"home_goals":hg,"away_goals":ag,"league":"","date":""})
    return matches

def save_history(matches):
    os.makedirs(os.path.dirname(DB_FILE), exist_ok=True)
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump({"matches": matches, "count": len(matches), "updated": datetime.now().isoformat()}, f, ensure_ascii=False, indent=1)

def add_match(match):
    """添加一场新比赛到历史库"""
    matches = load_history()
    key = f"{match['home_team']}|{match['away_team']}|{match.get('date','')}"
    for m in matches:
        if f"{m['home_team']}|{m['away_team']}|{m.get('date','')}" == key:
            return
    matches.append(match)
    save_history(matches)

if __name__ == "__main__":
    m = load_history()
    print(f"Loaded {len(m)} matches")
