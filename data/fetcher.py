"""data/fetcher.py -- 500.com 解析修复"""

import requests, json, re, os, time
from datetime import datetime


# Venue lookup
try:
    from data.venue_db import get_venue
except:
    def get_venue(t): return ""
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "zh-CN,zh;q=0.9",
}

RAW_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "raw")
CACHE_FILE = os.path.join(RAW_DIR, "matches_cache.json")

def fetch_500():
    """500.com: 比赛行结构为 联赛名 | 主队 | 客队 | ... | 赔率"""
    matches = []
    for url in ["https://trade.500.com/jczq/", "http://trade.500.com/jczq/"]:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.encoding = "gb2312"
            html = resp.text
            if len(html) < 5000: continue

            # 每个 tr 是一场比赛
            rows = re.findall(r'<tr[^>]*?>.*?</tr>', html, re.DOTALL)
            for row in rows:
                # 至少要有 3 个赔率数字
                odds = re.findall(r'>(\d+\.\d+)<', row)
                if len(odds) < 3: continue

                # 提取所有链接文字（联赛、球队等）
                links = re.findall(r'<a[^>]*?>([^<]+)</a>', row)
                if len(links) < 3: continue

                # 500.com 结构：links[0]=联赛, links[1]=主队, links[2]=客队
                league = links[0].strip()
                home = links[1].strip()
                away = links[2].strip()

                # 过滤非比赛行
                skip = ["退出","个人中心","全选","反选","保存方案","返回修改",
                       "设为首页","网站地图","我的彩票","登录","注册","开奖",
                       "首页","竞彩足球","北京单场","胜负彩","任选九"]
                if any(w in row for w in skip): continue
                if len(home) < 2 or len(away) < 2: continue
                if len(home) > 30 or len(away) > 30: continue
                # 球队名不能是联赛名格式（纯中文长串）
                if re.match(r'^[\u4e00-\u9fff]{4,}$', home) and home in ["世界杯","欧洲杯","英超","西甲","德甲","意甲","法甲","欧冠","欧联杯","美洲杯"]:
                    # 第一个链接是联赛，球队在后面
                    if len(links) >= 5:
                        home = links[2].strip()
                        away = links[3].strip()
                    else:
                        continue

                try:
                    matches.append({
                        "home_team": home, "away_team": away,
                        "venue": get_venue(home) or get_venue(away) or "", "league": league,
                        "home_odds": float(odds[0]),
                        "draw_odds": float(odds[1]),
                        "away_odds": float(odds[2]),
                    })
                except: pass

            valid = []; garbage_set = {"置顶","世界杯","友谊赛","日职","瑞典超","芬超","挪超","欧冠","欧联杯","英超","西甲","德甲","意甲","法甲","中超","K联赛","沙特联","美洲杯","亚冠","欧洲杯","欧国联","首页","开奖","登录","注册","个人中心","退出","全选","反选"}
            league_names = {"世界杯","欧洲杯","美洲杯","欧冠","亚冠","英超","西甲","德甲","意甲","法甲","欧联杯","欧国联","中超","日职","K联赛","澳超","荷甲","葡超","土超","俄超","芬超","瑞典超","挪超","沙特联"}
            for mm_ in matches:
                ht,at=mm_["home_team"],mm_["away_team"]
                if ht in garbage_set or at in garbage_set: continue
                if len(ht)>25 or len(at)>25: continue
                if len(ht)<2 or len(at)<2: continue
                if ht==at: continue
                if ht in league_names or at in league_names: continue
                valid.append(mm_)
            matches = valid
            if matches:
                print(f"  [500.com] {len(matches)} 场")
                return matches
        except Exception as e:
            print(f"  [500.com] {url}: {e}")
    return matches

def fetch_sporttery():
    matches = []
    for scheme in ["https","http"]:
        try:
            api = f"{scheme}://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchV1.qry"
            resp = requests.get(api, params={"matchType":1,"pageSize":200,"pageNo":1}, headers=HEADERS, timeout=12)
            if resp.status_code != 200: continue
            data = resp.json()
            items = data.get("value",{}).get("matchList",[])
            for m in items:
                oh = m.get("oddsHistory",{})
                had = oh.get("hadList",[{}])[-1] if oh.get("hadList") else {}
                matches.append({
                    "home_team": m.get("homeTeam",""), "away_team": m.get("awayTeam",""),
                    "league": m.get("leagueName",""),
                    "match_time": m.get("matchTime","") or m.get("matchDate",""),
                    "home_odds": float(had["h"]) if had.get("h") else None,
                    "draw_odds": float(had["d"]) if had.get("d") else None,
                    "away_odds": float(had["a"]) if had.get("a") else None,
                    "venue": m.get("venue","") or get_venue(m.get("homeTeam","")) or "", "league": m.get("leagueName",""),
                })
            if matches:
                print(f"  [竞彩网] {len(matches)} 场")
                return matches
        except Exception as e:
            print(f"  [竞彩网] {scheme}: {e}")
    return matches

def load_or_fetch(force_refresh=False):
    os.makedirs(RAW_DIR, exist_ok=True)
    if not force_refresh and os.path.exists(CACHE_FILE):
        mtime = os.path.getmtime(CACHE_FILE)
        if (time.time()-mtime) < 3600:
            with open(CACHE_FILE,"r",encoding="utf-8") as f:
                data = json.load(f)
            if data.get("upcoming"):
                print(f"[缓存] {len(data['upcoming'])} 待开赛")
                return data

    print("[抓取] 尝试线上...")
    upcoming = []; errors = []
    for fn, name in [(fetch_500,"500.com"),(fetch_sporttery,"竞彩网")]:
        try:
            r = fn()
            if r: upcoming = r; print(f"[抓取] {name}: {len(r)} 场"); break
        except Exception as e: errors.append(f"{name}:{e}")

    data = {"upcoming":upcoming,"history":[],"fetched_at":datetime.now().isoformat(),"errors":errors if not upcoming else []}
    with open(CACHE_FILE,"w",encoding="utf-8") as f:
        json.dump(data,f,ensure_ascii=False,indent=2)
    if not upcoming: print(f"[抓取] 失败: {errors}")
    return data

def get_upcoming_matches(): return load_or_fetch().get("upcoming",[])
def get_historical_matches(): return load_or_fetch().get("history",[])

def get_fetch_status():
    d = load_or_fetch()
    return {"upcoming_count":len(d.get("upcoming",[])),"history_count":len(d.get("history",[])),"errors":d.get("errors",[])}
