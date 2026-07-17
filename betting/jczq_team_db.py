# betting/jczq_team_db.py — 球队数据库 (FIFA排名 + Elo + 攻防数据)
"""球队数据库：每支球队包含 FIFA排名、Elo评分、场均进球、场均失球、近期胜率。"""

AVG_WC_GOALS = 1.32
AVG_CLUB_GOALS = {
    "英超": 1.42, "西甲": 1.30, "德甲": 1.54, "意甲": 1.32, "法甲": 1.40,
    "中超": 1.38, "日职": 1.30, "K联赛": 1.25, "沙特联": 1.45,
    "欧冠": 1.38, "欧联杯": 1.33, "芬超": 1.22, "default": 1.35,
}

# ===========================
# 国家队
# ===========================
NATIONAL_TEAM_DB = {
    "阿根廷":{"fifa":1,"elo":2135,"gf":1.90,"ga":0.55,"form":0.82,"wcExp":1.0},
    "法国":{"fifa":2,"elo":2100,"gf":2.10,"ga":0.65,"form":0.78,"wcExp":1.0},
    "巴西":{"fifa":3,"elo":2085,"gf":1.85,"ga":0.60,"form":0.75,"wcExp":1.0},
    "英格兰":{"fifa":4,"elo":2065,"gf":1.80,"ga":0.55,"form":0.76,"wcExp":0.95},
    "葡萄牙":{"fifa":6,"elo":2045,"gf":2.05,"ga":0.70,"form":0.80,"wcExp":0.9},
    "西班牙":{"fifa":8,"elo":2030,"gf":2.00,"ga":0.60,"form":0.78,"wcExp":1.0},
    "德国":{"fifa":9,"elo":2015,"gf":2.15,"ga":0.80,"form":0.68,"wcExp":1.0},
    "意大利":{"fifa":10,"elo":2010,"gf":1.60,"ga":0.55,"form":0.72,"wcExp":1.0},
    "荷兰":{"fifa":7,"elo":2035,"gf":2.00,"ga":0.70,"form":0.73,"wcExp":0.95},
    "克罗地亚":{"fifa":11,"elo":2000,"gf":1.45,"ga":0.70,"form":0.68,"wcExp":0.95},
    "比利时":{"fifa":5,"elo":2050,"gf":1.85,"ga":0.65,"form":0.70,"wcExp":0.9},
    "乌拉圭":{"fifa":12,"elo":1985,"gf":1.65,"ga":0.65,"form":0.67,"wcExp":0.9},
    "摩洛哥":{"fifa":14,"elo":1950,"gf":1.30,"ga":0.60,"form":0.70,"wcExp":0.65},
    "日本":{"fifa":15,"elo":1920,"gf":1.80,"ga":0.75,"form":0.78,"wcExp":0.7},
    "韩国":{"fifa":23,"elo":1785,"gf":1.75,"ga":1.02,"form":0.73,"wcExp":0.7},
    "伊朗":{"fifa":18,"elo":1840,"gf":1.55,"ga":0.80,"form":0.75,"wcExp":0.65},
    "沙特阿拉伯":{"fifa":48,"elo":1640,"gf":1.10,"ga":1.40,"form":0.50,"wcExp":0.55},
    "澳大利亚":{"fifa":24,"elo":1770,"gf":1.50,"ga":1.05,"form":0.68,"wcExp":0.65},
    "塞内加尔":{"fifa":17,"elo":1860,"gf":1.40,"ga":0.80,"form":0.72,"wcExp":0.65},
    "喀麦隆":{"fifa":35,"elo":1720,"gf":1.20,"ga":1.05,"form":0.55,"wcExp":0.6},
    "加纳":{"fifa":37,"elo":1710,"gf":1.15,"ga":1.10,"form":0.52,"wcExp":0.55},
    "尼日利亚":{"fifa":30,"elo":1740,"gf":1.35,"ga":0.95,"form":0.60,"wcExp":0.6},
    "埃及":{"fifa":33,"elo":1730,"gf":1.20,"ga":0.90,"form":0.58,"wcExp":0.55},
    "科特迪瓦":{"fifa":38,"elo":1705,"gf":1.25,"ga":1.00,"form":0.57,"wcExp":0.5},
    "南非":{"fifa":65,"elo":1500,"gf":0.95,"ga":1.45,"form":0.40,"wcExp":0.4},
    "突尼斯":{"fifa":28,"elo":1755,"gf":1.10,"ga":0.85,"form":0.60,"wcExp":0.5},
    "阿尔及利亚":{"fifa":34,"elo":1715,"gf":1.25,"ga":0.95,"form":0.55,"wcExp":0.5},
    "捷克":{"fifa":36,"elo":1715,"gf":1.45,"ga":1.15,"form":0.58,"wcExp":0.55},
    "墨西哥":{"fifa":18,"elo":1820,"gf":1.70,"ga":1.05,"form":0.65,"wcExp":0.75},
    "美国":{"fifa":16,"elo":1880,"gf":1.65,"ga":0.85,"form":0.70,"wcExp":0.7},
    "加拿大":{"fifa":25,"elo":1760,"gf":1.80,"ga":1.05,"form":0.72,"wcExp":0.35},
    "哥伦比亚":{"fifa":13,"elo":1970,"gf":1.50,"ga":0.70,"form":0.72,"wcExp":0.7},
    "智利":{"fifa":40,"elo":1680,"gf":1.20,"ga":1.20,"form":0.45,"wcExp":0.65},
    "厄瓜多尔":{"fifa":26,"elo":1750,"gf":1.20,"ga":1.05,"form":0.55,"wcExp":0.45},
    "秘鲁":{"fifa":31,"elo":1735,"gf":1.05,"ga":1.10,"form":0.48,"wcExp":0.5},
    "巴拉圭":{"fifa":42,"elo":1665,"gf":0.95,"ga":1.15,"form":0.42,"wcExp":0.55},
    "丹麦":{"fifa":20,"elo":1810,"gf":1.50,"ga":0.75,"form":0.65,"wcExp":0.65},
    "瑞典":{"fifa":27,"elo":1755,"gf":1.40,"ga":0.90,"form":0.55,"wcExp":0.7},
    "挪威":{"fifa":36,"elo":1725,"gf":1.80,"ga":0.95,"form":0.60,"wcExp":0.35},
    "瑞士":{"fifa":15,"elo":1820,"gf":1.60,"ga":0.95,"form":0.68,"wcExp":0.7},
    "奥地利":{"fifa":22,"elo":1800,"gf":1.65,"ga":0.85,"form":0.66,"wcExp":0.55},
    "波兰":{"fifa":32,"elo":1740,"gf":1.45,"ga":0.95,"form":0.58,"wcExp":0.6},
    "塞尔维亚":{"fifa":21,"elo":1805,"gf":1.55,"ga":1.00,"form":0.60,"wcExp":0.55},
    "土耳其":{"fifa":29,"elo":1750,"gf":1.50,"ga":1.10,"form":0.58,"wcExp":0.55},
    "乌克兰":{"fifa":19,"elo":1835,"gf":1.35,"ga":0.80,"form":0.63,"wcExp":0.5},
    "威尔士":{"fifa":39,"elo":1690,"gf":1.25,"ga":1.00,"form":0.50,"wcExp":0.5},
    "苏格兰":{"fifa":41,"elo":1675,"gf":1.30,"ga":1.10,"form":0.52,"wcExp":0.45},
    "匈牙利":{"fifa":33,"elo":1730,"gf":1.35,"ga":0.95,"form":0.58,"wcExp":0.5},
    "希腊":{"fifa":50,"elo":1620,"gf":1.10,"ga":0.95,"form":0.52,"wcExp":0.55},
    "爱尔兰":{"fifa":56,"elo":1575,"gf":1.10,"ga":1.05,"form":0.48,"wcExp":0.4},
    "芬兰":{"fifa":58,"elo":1560,"gf":1.15,"ga":1.20,"form":0.50,"wcExp":0.3},
    "冰岛":{"fifa":61,"elo":1530,"gf":1.05,"ga":1.20,"form":0.42,"wcExp":0.4},
    "斯洛伐克":{"fifa":44,"elo":1655,"gf":1.20,"ga":0.95,"form":0.55,"wcExp":0.45},
    "斯洛文尼亚":{"fifa":52,"elo":1600,"gf":1.15,"ga":1.00,"form":0.52,"wcExp":0.35},
    "波黑":{"fifa":50,"elo":1620,"gf":1.20,"ga":1.30,"form":0.48,"wcExp":0.35},
    "卡塔尔":{"fifa":48,"elo":1640,"gf":1.10,"ga":1.40,"form":0.50,"wcExp":0.4},
    "中国":{"fifa":74,"elo":1460,"gf":0.85,"ga":1.55,"form":0.32,"wcExp":0.2},
    "越南":{"fifa":88,"elo":1380,"gf":0.90,"ga":1.35,"form":0.40,"wcExp":0.15},
    "泰国":{"fifa":96,"elo":1340,"gf":0.95,"ga":1.40,"form":0.38,"wcExp":0.15},
    "海地":{"fifa":79,"elo":1420,"gf":0.80,"ga":1.60,"form":0.30,"wcExp":0.15},
    "库拉索":{"fifa":85,"elo":1390,"gf":0.75,"ga":1.70,"form":0.28,"wcExp":0.1},
}

# ===========================
# 俱乐部
# ===========================
CLUB_TEAM_DB = {
    "曼城":{"fifa":1,"elo":2150,"gf":2.20,"ga":0.55,"form":0.80},
    "阿森纳":{"fifa":2,"elo":2090,"gf":1.95,"ga":0.60,"form":0.76},
    "利物浦":{"fifa":3,"elo":2070,"gf":2.05,"ga":0.65,"form":0.74},
    "曼联":{"fifa":5,"elo":2030,"gf":1.70,"ga":0.75,"form":0.62},
    "切尔西":{"fifa":7,"elo":2010,"gf":1.80,"ga":0.70,"form":0.68},
    "巴萨":{"fifa":2,"elo":2120,"gf":2.15,"ga":0.60,"form":0.78},
    "皇马":{"fifa":1,"elo":2140,"gf":2.10,"ga":0.55,"form":0.80},
    "马竞":{"fifa":8,"elo":2000,"gf":1.65,"ga":0.65,"form":0.70},
    "拜仁":{"fifa":3,"elo":2100,"gf":2.30,"ga":0.70,"form":0.76},
    "多特蒙德":{"fifa":9,"elo":1990,"gf":1.85,"ga":0.85,"form":0.66},
    "勒沃库森":{"fifa":6,"elo":2040,"gf":2.00,"ga":0.70,"form":0.78},
    "国米":{"fifa":4,"elo":2070,"gf":1.85,"ga":0.60,"form":0.76},
    "AC米兰":{"fifa":8,"elo":2010,"gf":1.75,"ga":0.70,"form":0.68},
    "尤文图斯":{"fifa":11,"elo":1975,"gf":1.50,"ga":0.65,"form":0.62},
    "上海海港":{"fifa":30,"elo":1780,"gf":1.80,"ga":0.90,"form":0.72},
    "上海申花":{"fifa":35,"elo":1740,"gf":1.60,"ga":0.95,"form":0.66},
    "AC奥卢":{"fifa":200,"elo":1500,"gf":1.10,"ga":1.40,"form":0.40},
    "玛丽港":{"fifa":210,"elo":1480,"gf":1.00,"ga":1.50,"form":0.35},
}

def get_team(team_name: str) -> dict:
    """获取球队数据，先国家队库，再俱乐部库，找不到返回默认"""
    if team_name in NATIONAL_TEAM_DB:
        return NATIONAL_TEAM_DB[team_name]
    if team_name in CLUB_TEAM_DB:
        d = dict(CLUB_TEAM_DB[team_name])
        d.setdefault("wcExp", 0.5)
        return d
    return {"fifa": 80, "elo": 1400, "gf": 1.00, "ga": 1.35, "form": 0.40, "wcExp": 0.2}

def is_national_team(team_name: str) -> bool:
    return team_name in NATIONAL_TEAM_DB

def get_avg_goals(league: str = None) -> float:
    if league and league in AVG_CLUB_GOALS:
        return AVG_CLUB_GOALS[league]
    return AVG_WC_GOALS

ALL_NATIONAL_TEAMS = list(NATIONAL_TEAM_DB.keys())
ALL_CLUB_TEAMS = list(CLUB_TEAM_DB.keys())
