# lottery_fetcher.py — 体彩开奖数据抓取器
"""
抓取近期彩票开奖结果：超级大乐透、七星彩、排列三、排列五
数据来源: sporttery.cn API (大乐透/排列五) + 500.com HTML (七星彩/排列三)
"""

import re
import json
import time
import os as _os
import requests
from datetime import datetime, timedelta

# ============================================================
# Local cache
# ============================================================
CACHE_DIR = _os.path.join(_os.path.dirname(_os.path.abspath(__file__)), "data", "lottery_cache")
CACHE_TTL_HOURS = 4

def _ensure_cache_dir():
    _os.makedirs(CACHE_DIR, exist_ok=True)

def _cache_path(lottery_key):
    return _os.path.join(CACHE_DIR, f"{lottery_key}.json")

def _load_cache(lottery_key):
    path = _cache_path(lottery_key)
    if not _os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        fetched_at = data.get("fetched_at", "")
        if fetched_at:
            ft = datetime.fromisoformat(fetched_at)
            if datetime.now() - ft < timedelta(hours=CACHE_TTL_HOURS):
                return data.get("results", [])
    except:
        pass
    return None

def _save_cache(lottery_key, results):
    _ensure_cache_dir()
    path = _cache_path(lottery_key)
    data = {
        "fetched_at": datetime.now().isoformat(),
        "count": len(results),
        "results": results,
    }
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception as e:
        print(f"[cache] Save error: {e}")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "Accept": "application/json, text/html, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://www.lottery.gov.cn/",
}

# 彩种配置
LOTTERY_CONFIG = {
    "dlt": {
        "name": "超级大乐透",
        "icon": "🎱",
        "source": "sporttery_api",
        "gameNo": "85",
    },
    "qxc": {
        "name": "七星彩",
        "icon": "🌟",
        "source": "500_html",
        "url": "https://kaijiang.500.com/qxc.shtml",
    },
    "pls": {
        "name": "排列三",
        "icon": "🎯",
        "source": "500_html",
        "url": "https://kaijiang.500.com/pls.shtml",
    },
    "plw": {
        "name": "排列五",
        "icon": "🎲",
        "source": "sporttery_api",
        "gameNo": "350133",
    },
}


def _fetch_sporttery(game_no: str, page_size: int = 100) -> list:
    """从 sporttery.cn API 抓取开奖历史（支持多页）"""
    results = []
    seen = set()

    for page_no in range(1, 5):  # 最多抓4页
        url = (
            f"https://webapi.sporttery.cn/gateway/lottery/getHistoryPageListV1.qry"
            f"?gameNo={game_no}&provinceId=0&pageSize={page_size}&isVerify=1&pageNo={page_no}"
        )
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            resp.encoding = "utf-8"
            data = resp.json()
            if data.get("success") and data.get("value"):
                value = data["value"]
                # 第一页：lastPoolDraw + list
                if page_no == 1:
                    last = value.get("lastPoolDraw", {})
                    if last.get("lotteryDrawNum") and last["lotteryDrawNum"] not in seen:
                        results.append(_parse_sporttery_draw(last))
                        seen.add(last["lotteryDrawNum"])
                # 历史列表
                draw_list = value.get("list", [])
                if not draw_list:
                    break  # 没有更多数据
                for item in draw_list:
                    dn = item.get("lotteryDrawNum", "")
                    if dn and dn not in seen:
                        results.append(_parse_sporttery_draw(item))
                        seen.add(dn)
                # 如果返回数量少于 page_size，说明没有更多页
                if len(draw_list) < page_size:
                    break
            else:
                break
        except Exception as e:
            print(f"[lottery_fetcher] sporttery API error (gameNo={game_no}, page={page_no}): {e}")
            break

    return results


def _parse_sporttery_draw(item: dict) -> dict:
    """解析 sporttery API 返回的单期数据"""
    draw_num = item.get("lotteryDrawNum", "")
    draw_result = item.get("lotteryDrawResult", "")
    draw_time = item.get("lotteryDrawTime", "")
    pool_balance = item.get("poolBalanceAfterdraw", "")

    prizes = []
    for p in item.get("prizeLevelList", []):
        prizes.append({
            "level": p.get("prizeLevel", ""),
            "count": p.get("stakeCount", ""),
            "amount": p.get("stakeAmount", ""),
            "total": p.get("totalPrizeamount", ""),
        })

    return {
        "draw_num": draw_num,
        "numbers": draw_result.split() if draw_result else [],
        "date": draw_time,
        "pool_balance": pool_balance,
        "prizes": prizes,
    }


def _fetch_500html(url: str, max_draws: int = 15) -> list:
    """从 500.com 抓取开奖数据：先读首页拿最新期号，再逐期抓详情页"""
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.encoding = "gb2312"
        html = resp.text

        if len(html) < 3000:
            return []

        # 从首页提取最新期号和日期
        clean = re.sub(r'<[^>]+>', ' ', html)
        clean = re.sub(r'\s+', ' ', clean).strip()
        term_match = re.search(r'第\s*(\d+)\s*期', clean)
        
        if not term_match:
            # 首页被反爬，尝试从最新一期详情页反推期号
            # 排列三和排列五期号同步，这里用一个较大的起始值去试
            # 先尝试抓取一个近期详情页来确定最新期号
            found_term = None
            guess_terms = [26200, 26180, 26170]
            for guess in guess_terms:
                try:
                    test_url = f"https://kaijiang.500.com/shtml/pls/{guess}.shtml"
                    tr = requests.get(test_url, headers=HEADERS, timeout=10)
                    tr.encoding = "gb2312"
                    tclean = re.sub(r'<[^>]+>', ' ', tr.text)
                    tclean = re.sub(r'\s+', ' ', tclean)
                    tm = re.search(r'第\s*(\d+)\s*期', tclean)
                    if tm:
                        found_term = int(tm.group(1))
                        break
                except:
                    continue
            if not found_term:
                return []
            latest_term = found_term
        else:
            latest_term = int(term_match.group(1))
        
        # 确定彩种类型（从 URL 判断）
        is_qxc = "qxc" in url
        is_pls = "pls" in url and "plw" not in url
        lottery_type = "qxc" if is_qxc else "pls"
        
        results = []
        
        # 逐期抓取详情页
        for offset in range(max_draws):
            term = latest_term - offset
            if term <= 0:
                break
            
            detail_url = f"https://kaijiang.500.com/shtml/{lottery_type}/{term}.shtml"
            try:
                dr = requests.get(detail_url, headers=HEADERS, timeout=10)
                dr.encoding = "gb2312"
                dhtml = dr.text
                
                if len(dhtml) < 2000:
                    continue
                
                # 提取号码
                numbers = re.findall(r'<li class="ball_orange">(\d+)</li>', dhtml)
                if not numbers:
                    continue
                
                draw = {"numbers": numbers, "draw_num": str(term)}
                
                # 提取日期（中文格式：2026年6月30日）
                dclean = re.sub(r'<[^>]+>', ' ', dhtml)
                dclean = re.sub(r'\s+', ' ', dclean)
                date_m = re.search(r'(\d{4})年(\d{1,2})月(\d{1,2})日', dclean)
                if date_m:
                    draw["date"] = f"{date_m.group(1)}-{int(date_m.group(2)):02d}-{int(date_m.group(3)):02d}"
                
                # 第一期额外信息
                if offset == 0:
                    sales_m = re.search(r'销售金额[：:]\s*<[^>]*>([0-9,]+\.?\d*)', dhtml)
                    if sales_m:
                        draw["sales"] = sales_m.group(1) + "元"
                    pool_m = re.search(r'奖池滚存[：:]\s*<[^>]*>([0-9,]+\.?\d*)', dhtml)
                    if pool_m:
                        draw["pool_balance"] = pool_m.group(1) + "元"
                    prizes = []
                    prize_table = re.findall(
                        r'<tr align="center">\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>',
                        dhtml
                    )
                    for level, count, amount in prize_table:
                        level = level.strip()
                        if level and level not in ("奖项", "&nbsp;"):
                            prizes.append({
                                "level": level,
                                "count": count.strip(),
                                "amount": amount.strip(),
                            })
                    if prizes:
                        draw["prizes"] = prizes
                    type_m = re.search(r'号码类型[：:]\s*<[^>]*>([^<]+)', dhtml)
                    if type_m:
                        draw["number_type"] = type_m.group(1).strip()
                
                results.append(draw)
                
                # 速率控制
                time.sleep(0.05)
                
            except Exception as e:
                continue
        
        return results

    except Exception as e:
        print(f"[lottery_fetcher] 500.com error ({url}): {e}")
    return []


def fetch_lottery(lottery_key: str, count: int = 100, force_refresh: bool = False) -> list:
    """获取指定彩种近期开奖结果（优先本地缓存，缓存有效期4小时）"""
    config = LOTTERY_CONFIG.get(lottery_key)
    if not config:
        return []

    # 检查缓存
    if not force_refresh:
        cached = _load_cache(lottery_key)
        if cached:
            return cached

    # 从网络抓取
    if config["source"] == "sporttery_api":
        results = _fetch_sporttery(config["gameNo"], count)
    elif config["source"] == "500_html":
        results = _fetch_500html(config["url"], max_draws=min(count, 15))
    else:
        results = []

    # 保存到缓存
    if results:
        _save_cache(lottery_key, results)

    return results
def fetch_all(count: int = 100, force_refresh: bool = False) -> dict:
    """获取全部四种彩种的开奖结果（优先本地缓存）"""
    results = {}
    for key, config in LOTTERY_CONFIG.items():
        data = fetch_lottery(key, count, force_refresh=force_refresh)
        results[key] = {
            "name": config["name"],
            "icon": config["icon"],
            "data": data,
        }
    return results


if __name__ == "__main__":
    import json as _json
    all_data = fetch_all(count=5)
    print(_json.dumps(all_data, ensure_ascii=False, indent=2))
