# 足球预测系统 — Agent 交接文档

## 1. 项目概述

基于12种统计算法 + 机器学习的足球比赛预测系统。输入两支球队，输出胜平负概率、比分分布、进球数、半全场预测等。Web界面 (Flask + 原生JS)，数据来源为 500.com、竞彩网、FIFA API。

## 2. 快速启动

```bash
cd C:\Users\lenovo1\Documents\足彩
C:\Python314\python.exe run.py
```

浏览器访问 `http://127.0.0.1:5000`。命令行窗口可见启动日志，关窗口即停服。

## 3. 项目结构

```
足彩/
├── run.py                  # 入口
├── config.py               # 全局配置：联赛、ELO参数、权重、球队列表
├── requirements.txt        # 依赖：flask, numpy, requests, xgboost, joblib
├── web/
│   ├── app.py              # Flask 主路由 (~880行)
│   ├── templates/
│   │   ├── index.html      # 主页：球队选择 + 预测结果
│   │   ├── history.html    # 历史数据中心：比赛记录/交锋/趋势/ELO排名
│   │   ├── calibrate.html  # 校准系统界面
│   │   ├── rankings.html   # 独立ELO排名页（已重定向到 /history#rankings）
│   │   ├── bet_plan.html   # 竞彩投注计划
│   │   └── lottery.html    # 彩票（独立功能）
│   └── static/
│       ├── css/style.css   # 全局样式（暗色主题，CSS变量）
│       └── js/
│           ├── app.js      # 前端主逻辑 (~1016行)：球队选择、预测渲染、侧边栏
│           └── chart.umd.min.js  # Chart.js
├── models/                 # 12个预测模型（每个一个文件）
│   ├── poisson.py          # 泊松分布：λ=攻×防×联赛均值
│   ├── dixon_coles.py      # Dixon-Coles：泊松改进版，修正低比分相关
│   ├── elo.py              # ELO等级分：赛后动态调整
│   ├── massey.py           # Massey排名：线性方程组求解
│   ├── form.py             # 近期状态：8场衰减加权
│   ├── head_to_head.py     # 交锋记录
│   ├── market_odds.py      # 市场赔率：去水头归一化
│   ├── knn_similar.py      # K近邻相似匹配
│   ├── xgboost_model.py    # XGBoost集成（需joblib）
│   ├── neural_net.py       # 纯numpy神经网络（3层全连接）
│   ├── monte_carlo.py      # 蒙特卡洛10000次模拟
│   └── bayesian_hierarchical.py  # 贝叶斯层次模型
├── ensemble/
│   ├── bma.py              # 贝叶斯模型平均：动态加权融合
│   ├── stacker.py          # Stacking集成
│   ├── weights.json        # 各模型当前权重
│   └── saved_models/       # XGBoost 已训练模型文件
├── features/
│   ├── builder.py          # 特征向量构建器 (18维)
│   └── player_impact.py    # 球员影响评估
├── data/
│   ├── fetcher.py          # 线上数据抓取 (500.com/竞彩网)
│   ├── history_db.py       # 历史比赛数据库读写
│   ├── venue_db.py         # 场地数据库
│   ├── fifa_collector.py   # FIFA API数据采集
│   ├── collect_nt.py       # 国家队数据采集
│   ├── processed/          # 处理后数据 (ELO评分、团队元数据等)
│   └── raw/                # 原始缓存数据
├── betting/                # 竞彩投注模块（辅助功能）
├── calibrate.py            # 离线校准：回测+权重优化
├── calibrate_cli.py        # 校准CLI界面
└── _mkrank.py / debug_*.py / patch_models.py  # 一次性脚本，可忽略
```

## 4. 核心数据流

```
用户选择球队 → POST /predict
  → _init_models() 初始化所有模型（首次调用时）
  → 12个模型各自预测
  → BMA.blend() 加权融合
  → 返回 JSON: { home_win, draw, away_win, expected_total_goals, ... }
  → 前端 app.js renderResults() 渲染结果
```

**侧边栏数据**: 页面加载时调用 `/api/upcoming`（近期比赛）+ `/api/wc_matches`（世界杯赛果），数据来自缓存文件。

## 5. 12个模型说明

| 模型 | 文件 | 原理 | 当前状态 |
|------|------|------|----------|
| 泊松分布 | poisson.py | λ主=攻主×防客×联赛均值 | ✅ 正常 |
| Dixon-Coles | dixon_coles.py | 泊松+低比分相关性校正 | ✅ 正常 |
| ELO等级分 | elo.py | 动态评分，P=1/(1+10^(-Δ/400)) | ✅ 正常 |
| Massey排名 | massey.py | 线性方程组求解实力值 | ✅ 正常 |
| 近期状态 | form.py | 近8场衰减加权 | ✅ 正常 |
| 交锋记录 | head_to_head.py | 历史对战统计，无交锋时排除 | ✅ 正常 |
| 市场赔率 | market_odds.py | 赔率反推概率，有真实赔率时自动3.5x加权 | ⚠️ 无赔率时排除 |
| K近邻相似 | knn_similar.py | 特征最近邻投票 | ✅ 正常 |
| XGBoost集成 | xgboost_model.py | 200棵树分类+回归，时间分割训练避免前视偏差 | ✅ 已训练 |
| 神经网络 | neural_net.py | 3层(18-32-16-3) SGD训练 | ⚠️ 加载可能失败，有try/catch保护 |
| 蒙特卡洛 | monte_carlo.py | 10000次模拟统计频率 | ✅ 正常 |
| 贝叶斯层次 | bayesian_hierarchical.py | 先验+似然→后验 | ✅ 正常 |

## 6. 关键 API 路由

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 主页 |
| `/predict` | POST | 预测（核心）：接收 home_team, away_team, league, neutral, [home_odds, draw_odds, away_odds] |
| `/api/debug_predict` | POST | 调试预测：返回原始数据+各模型计算步骤 |
| `/api/upcoming` | GET | 近期比赛列表（含赔率） |
| `/api/wc_matches` | GET | 2026世界杯已完成比赛 |
| `/api/rankings` | GET | ELO排名：{national:[], clubs:[], total_teams, national_count, club_count} |
| `/api/history/matches` | GET | 历史比赛（分页）：?page=&league=&team= |
| `/api/history/h2h` | GET | 交锋记录：?a=&b= |
| `/api/history/trend` | GET | 球队趋势：?team=&n=20 |
| `/api/refresh_data` | GET | 刷新线上数据 |
| `/api/sync_fifa` | GET | 同步FIFA世界杯数据 |
| `/api/calibrate/run` | GET | 启动后台校准 |
| `/api/calibrate/status` | GET | 查询校准进度 |
| `/history` | GET | 历史数据中心页面 |
| `/rankings` | GET | → 重定向到 /history#rankings |
| `/calibrate` | GET | 校准页面 |

## 7. 前端架构 (app.js)

- **球队选择**: 搜索框+下拉菜单，三个标签（国家队/俱乐部/全部），使用 Jinja2 注入的全局变量 NATIONAL_TEAMS, CLUB_TEAMS, ALL_TEAMS
- **侧边栏**: loadSidebar() 加载近期比赛，loadWcMatches() 加载世界杯赛果，点击比赛自动填入球队并触发预测
- **预测渲染**: renderResults() 渲染饼图+各模型表格+比分概率+半全场
- **模型介绍弹窗**: 点击模型名称弹出 showModelInfo()
- **模型名称映射**: MODEL_NAMES / MODEL_DESC 对象（中文）

## 8. 已知问题 / 注意事项

1. **中文编码**: 所有文件 UTF-8。PowerShell 操作中文路径时用 Python 脚本而非直接 cmd。
2. **Flask 开发服务器**: 单线程，首次 `/predict` 请求触发训练时可能阻塞 ~5-15 秒。
3. **NeuralNet**: numpy手写网络容易过拟合，predict()有try/catch降级处理。
4. **Market odds**: 用户手动选队时不传赔率→被排除；从侧边栏点击比赛会自动填入赔率。
5. **缓存**: index.html 中的 JS/CSS 引用带 `?v=` 版本号防缓存。修改静态文件后需更新版本号。
6. **校准系统**: calibrate.py 后台运行，状态写入 `data/processed/calibration_status.json`。
7. **500.com 抓取**: jczq_fetcher.py 解析 GB2312 编码的HTML，结构脆弱，网站改版可能失效。
8. **Windows**: 路径 `C:\Users\lenovo1\Documents\足彩`，Python `C:\Python314\python.exe`。
9. **桌面快捷方式**: `启动服务器.bat` 在项目根目录，编码问题导致中文显示乱码但功能正常。

## 9. 重启服务器流程

```powershell
Get-Process -Name python | Stop-Process -Force
Start-Sleep 1
Start-Process -FilePath "C:\Python314\python.exe" -ArgumentList "run.py" -WorkingDirectory "C:\Users\lenovo1\Documents\足彩"
```

或用可见窗口：
```powershell
Start-Process -FilePath "C:\Python314\python.exe" -ArgumentList "run.py" -WorkingDirectory "C:\Users\lenovo1\Documents\足彩" -WindowStyle Normal
```

## 10. 常见调试命令

```powershell
# 测试API是否正常
python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:5000/api/status',timeout=10).read().decode())"

# 测试预测
python -c "import urllib.request,json; b=json.dumps({'home_team':'加拿大','away_team':'摩洛哥','league':'世界杯','neutral':True,'task_id':'t'}).encode(); r=urllib.request.Request('http://127.0.0.1:5000/predict',data=b,headers={'Content-Type':'application/json'}); print(json.loads(urllib.request.urlopen(r,timeout=60).read())['home_win'])"

# 检查端口
netstat -ano | findstr 5000
```
## 11. 彩票预测模块

独立于足球预测的体彩分析功能，涵盖超级大乐透、七星彩、排列三、排列五四种彩种。

### 11.1 文件结构

| 文件 | 说明 |
|------|------|
| lottery_fetcher.py | 数据抓取：sporttery.cn API (大乐透/排列五) + 500.com 逐期详情页 (七星彩/排列三) |
| lottery_predictor.py | 预测引擎：时间衰减加权、5策略并行、回测验证、自适应选策 |
| web/templates/lottery.html | 前端页面：左右双栏（开奖结果 + 预测分析），纯JS无框架 |
| data/lottery_cache/ | 本地缓存目录：dlt.json / qxc.json / pls.json / plw.json，有效期4小时 |

### 11.2 5种预测策略

| 策略 | 函数 | 原理 |
|------|------|------|
| hot 热号追踪 | _strategy_hot | 选时间衰减加权频率最高的号码 |
| cold 冷号反弹 | _strategy_cold | 选频率最低的号码（赌反弹） |
| weighted 综合加权 | _strategy_weighted | 按衰减频率加权随机抽样（无放回） |
| missing 遗漏回补 | _strategy_missing | 优先选最久未出的号码 |
| pattern 模式匹配 | _strategy_pattern | 奇偶比/大小比分析 + 相邻位转移概率(马尔可夫) |

### 11.3 时间衰减加权

近期数据权重指数衰减，半衰期12期。回测保留最近10期测试，其余作训练集，滑动窗口滚动验证。

### 11.4 数据来源

| 彩种 | 数据源 | 数据量 | 抓取方式 |
|------|--------|--------|----------|
| 超级大乐透 | sporttery.cn API (gameNo=85) | ~400期 | 分页JSON API，每页100条 |
| 七星彩 | 500.com 逐期详情页 | ~15期 | 主页取最新期号 -> 循环抓取详情页 |
| 排列三 | 500.com 逐期详情页 | ~15期 | 同上，期号与排列五同步 |
| 排列五 | sporttery.cn API (gameNo=350133) | ~400期 | 分页JSON API |

注意：七星彩和排列三的 sporttery.cn API (gameNo=04/35) 被腾讯云WAF拦截返回403，只能走500.com逐页抓取，首次约10秒，后续命中本地缓存秒返。

### 11.5 API 路由

| 路由 | 方法 | 说明 |
|------|------|------|
| /lottery | GET | 彩票页面 |
| /api/lottery | GET | 获取四种彩种最新开奖数据，支持 ?force=1 强制刷新 |
| /api/lottery/predict/<key> | GET | 获取指定彩种预测分析，key: dlt/qxc/pls/plw |

### 11.6 大乐透回本率优化

大乐透最低奖九等奖5元（后区全中2个即可），是回本关键。策略：全量评分66种后区组合（时间衰减+频率+遗漏gap），多样性筛选，前区从Top20加权采样。50期回测ROI约23%（随机约8%），72%期数有奖。

### 11.7 桌面文件

预测文件保存到桌面，开奖后对比修正策略权重。综合评分 = 回测x70% + 实战x30%。桌面仅保留最新有效文件。

### 11.8 常见操作

```
# 强制刷新所有彩种缓存
python -c "from lottery_fetcher import fetch_all; fetch_all(100, force_refresh=True)"

# 测试大乐透预测
python -c "from lottery_fetcher import fetch_lottery; from lottery_predictor import full_analysis; h=fetch_lottery('dlt',100); a=full_analysis('dlt',h); print(a['best_strategy'], a['backtests'])"

# 检查缓存
dir data\lottery_cache\
```

## 12. 竞彩投注智能分析模块

基于 3 模型集成（Poisson 55% + Elo 15% + FIFA 30%）的正期望值（+EV）投注策略生成器。从 500.com 实时抓取多玩法赔率（SPF/RQSPF/总进球），计算算法概率与市场隐含概率的偏差，筛选 +EV 选项并按用户本金自动分配投注方案。

### 12.1 文件结构

| 文件 | 说明 |
|------|------|
| bet_jczq.py | CLI 主入口，支持 `--live` 实时抓取、`--budget` 本金、`--upset` 冷门因子、`--output` 输出文件 |
| betting/jczq_team_db.py | 球队数据库：60+ 国家队 + 18 俱乐部，含 FIFA 排名、Elo 评分、场均进球/失球、近期胜率、世界杯经验分 |
| betting/jczq_engine.py | 算法核心：Poisson 攻防强度法 + Elo 评级模型 + FIFA 排名模型 → 三模型加权集成 → 冷门因子调整 → 让球盘/总进球概率 → EV 计算 |
| betting/jczq_fetcher.py | 500.com 多玩法抓取：playid=269(SPF) / 312(RQSPF) / 270(总进球)，解析 GB2312 HTML，15 分钟本地缓存 |
| betting/jczq_planner.py | 投注方案生成：+EV 筛选、单关/串关约束、按 EV%×√概率 加权分配本金、格式化为可读报告 |
| betting/plan_output.txt | 最近一次运行输出的方案文本文件 |
| web/templates/bet_plan.html | 前端页面：冷门因子滑块、本金输入框、示例数据开关、投注明细表、完整报告 |

### 12.2 算法流程

```
fetch_all_odds()  # 抓取 SPF + RQSPF + 总进球赔率
  → parse_matches()  # 解析为 [{id, league, time, home, away, singleBet, odds}]
  → JczqEngine.analyze(home, away)
      → Poisson: λ主 = (gf_h/avgWC) × (ga_a/avgWC) × avgWC × 0.94 × (form_h/0.65)
      → Elo: P = 1/(1+10^(-Δ/400)), draw_base=0.26
      → FIFA: goalDiff = -(rank_a-rank_h)×0.013, P = 1/(1+e^(goalDiff/σ×2.5))
      → Ensemble: [0.55, 0.15, 0.30] 加权 + 归一化
      → 冷门因子: shift = upset×(0.5+|H-A|), 从热门向冷门偏移
      → RQSPF: Poisson 联合分布枚举 0-7 球, 计算强队-1盘三种结果概率
      → 总进球: P(k) = Σ_{g1+g2=k} Poisson(g1|λH)×Poisson(g2|λA)
  → calc_ev(): algoProb × odds - 1, 市场隐含概率 = (1/odds)/overround
  → find_ev_bets(): 仅取 EV>0, 单关检查 singleBet 标记, 让球盘全部 parlayOnly
  → allocate_budget(): 权重 = EV%×√prob, 按权重分配本金到 unit_price 倍数, 微调至精确
```

### 12.3 三种玩法

| 玩法 | playid | 可单关 | 说明 |
|------|--------|--------|------|
| SPF 胜平负 | 269 | 标记"单关"的场次 | 3 个选项, 取前 3 个赔率数字 |
| RQSPF 让球胜平负 | 312 | 全部仅串关 | 强队视角 -1 盘, 第 4-6 个赔率 |
| 总进球 | 270 | 标记"单关"的场次 | 8 个选项 (0球~7+球) |

### 12.4 约束条件

- 只投算法概率 > 市场隐含概率的 +EV 选项
- 单关仅限标记了"单关"的场次和玩法
- 让球盘全部仅串关（不检查单关标记）
- 按 `EV% × √(算法概率)` 加权分配，兼顾期望值和可实现性
- 每注 2 元，每张票金额为 2 的倍数
- 预期回报 = 本金 × (1+EV)，非最大回报

### 12.5 冷门因子

`--upset` 参数（0.05~0.18，默认 0.12）。从强队概率向弱队转移，转移量 = upset × (0.5 + |H-A|)。调高更激进（多投冷门），调低更保守。

### 12.6 API 路由

| 路由 | 方法 | 说明 |
|------|------|------|
| /betting | GET | 投注分析页面 |
| /api/betting/analyze | POST | 运行完整分析，参数: `{upset, use_sample, budget}` |
| /api/betting/matches | GET | 获取比赛列表（预加载用） |

### 12.7 常见操作

```powershell
# 快速演示（4 场示例数据）
python bet_jczq.py --quick

# 实时抓取 500.com
python bet_jczq.py --live

# 自定义本金和冷门因子
python bet_jczq.py --budget 200 --upset 0.15

# 输出方案到文件
python bet_jczq.py --output betting/my_plan.txt

# 测试引擎单场分析
python -c "from betting.jczq_engine import JczqEngine; e=JczqEngine(); r=e.analyze('捷克','南非'); print(r['spf'])"

# API 测试
python -c "import urllib.request,json; b=json.dumps({'upset':0.12,'use_sample':True,'budget':100}).encode(); r=urllib.request.Request('http://127.0.0.1:5000/api/betting/analyze',data=b,headers={'Content-Type':'application/json'}); d=json.loads(urllib.request.urlopen(r,timeout=60).read()); print(d['total_return'],'元')"
```

### 12.8 注意事项

1. 球队数据（FIFA 排名、Elo、场均进球/失球）需定期手动更新，文件 `betting/jczq_team_db.py`
2. 俱乐部赛事 AVG_CLUB_GOALS 按联赛区分（英超 1.42、西甲 1.30、德甲 1.54 等），芬超等非主流联赛使用国家队基准
3. 500.com 页面结构变化可能导致抓取失败，此时自动降级为示例数据
4. 预期总回报为数学期望值（各注 stake×(1+EV) 之和），非所有投注同时命中的最大回报
5. 加权平均 EV 较高（~30%）属模型特征，反映算法与市场分歧度，不作为实际收益保证