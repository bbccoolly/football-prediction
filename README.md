# ⚽ 足球比赛预测系统

基于 **11 个独立候选模型** 的足球比赛预测系统，融合泊松分布、ELO 评级、贝叶斯层次模型等，覆盖胜平负、让球胜负、比分、进球数、半全场等预测。蒙特卡洛只对最终融合结果进行派生采样；未训练、无真实输入或输出非法的模型不会参与最终融合。

## 功能特性

| 功能 | 说明 |
|------|------|
| 🎯 11 个独立候选模型 | 泊松 / Dixon-Coles / ELO / Massey / 近期状态 / 交锋记录 / 市场赔率 / KNN / XGBoost / 神经网络 / 贝叶斯层次；蒙特卡洛作为融合后的派生模拟 |
| 📊 动态融合 | 基于 Brier Score 的启发式动态权重，并按本次有效模型重新归一化 |
| 🎰 让球胜负 | ±1 / ±1.5 / ±2 共 7 个盘口的概率计算 |
| 📈 半全场 | 半场/全场组合概率分布 |
| 🏟️ 场地因素 | 自动识别世界杯等中立场地，主场优势分联赛配置 |
| 👥 球员缺阵 | 勾选排除缺阵球员，调整阵容完整度 |
| 📋 侧边栏 | 近期比赛一键点击直接预测 |
| 🔍 计算过程 | 查看每个模型的公式、中间结果、融合权重 |
| 🎲 竞彩投注 | 基于算法概率的 EV 分析 + 过关方案规划 |
| 🎰 彩票预测 | 大乐透 / 排列三 / 排列五 / 七星彩统计分析 |

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 安装

```bash
# 克隆仓库
git clone https://github.com/bbccoolly/football-prediction.git
cd football-prediction

# 安装依赖
pip install -r requirements.txt
```

开发与测试依赖：

```bash
python -m pip install -r requirements-dev.txt
pytest -q
```

### 启动

```bash
python run.py
```

浏览器打开 **http://127.0.0.1:5000**

刷新数据、FIFA 同步、校准和彩票强制刷新需要管理令牌：

```powershell
$env:FOOTBALL_ADMIN_TOKEN = "your-secret-token"
python run.py
```

默认只监听 `127.0.0.1:5000`。可通过 `FOOTBALL_HOST` 和 `FOOTBALL_PORT` 修改；监听非回环地址时必须配置管理令牌。

> 首次启动会尝试抓取 OpenLigaDB 单赛季德甲和 500.com 近期完场数据，通常为 300 场以上；数量和耗时取决于网络与源站可用性。

### 命令行模式

```bash
# 交互式 CLI
python main.py

# 启动 Web 服务
python main.py web
```

## 项目结构

```
football-prediction/
│
├── web/                          # Flask 前端
│   ├── app.py                    #   主服务 · 全部 API 路由
│   ├── templates/
│   │   ├── index.html            #   预测主页
│   │   ├── history.html          #   历史记录 + ELO 排名
│   │   ├── bet_plan.html         #   竞彩投注方案
│   │   ├── calibrate.html        #   模型校准界面
│   │   └── lottery.html          #   彩票开奖查询
│   └── static/
│       ├── css/style.css         #   深色主题 UI
│       └── js/app.js             #   前端交互逻辑
│
├── models/                       # 11 个独立模型 + 蒙特卡洛派生模拟
│   ├── poisson.py                #   泊松分布（让球·半全场·比分）
│   ├── dixon_coles.py            #   Dixon-Coles 低分修正
│   ├── elo.py                    #   ELO 动态评级系统
│   ├── massey.py                 #   Massey 最小二乘排名
│   ├── form.py                   #   近期状态分析
│   ├── head_to_head.py           #   历史交锋统计
│   ├── market_odds.py            #   真实市场赔率
│   ├── knn_similar.py            #   KNN 相似比赛匹配
│   ├── xgboost_model.py          #   XGBoost 梯度提升
│   ├── neural_net.py             #   3 层全连接神经网络
│   ├── monte_carlo.py            #   蒙特卡洛 10000 次模拟
│   └── bayesian_hierarchical.py  #   贝叶斯层次模型
│
├── ensemble/                     # 融合层
│   ├── bma.py                    #   基于 Brier Score 的启发式动态融合
│   ├── prediction_contract.py    #   模型可用性与概率协议
│   └── stacker.py                #   Stacking 元学习器
│
├── features/                     # 特征工程
│   ├── builder.py                #   18 维特征向量构造器
│   └── player_impact.py          #   球员影响力评估
│
├── data/                         # 数据层
│   ├── fetcher.py                #   500.com / 竞彩网实时抓取
│   ├── history_db.py             #   历史比赛数据库
│   └── venue_db.py               #   场地数据库
│
├── betting/                      # 竞彩投注
│   ├── jczq_engine.py            #   投注分析引擎
│   ├── jczq_planner.py           #   投注方案规划
│   ├── jczq_fetcher.py           #   赔率实时抓取
│   └── jczq_team_db.py           #   球队数据
│
├── config.py                     # 全局配置（15 联赛 · 128 球队 · 算法参数）
├── calibrate.py                  # 回测校准主程序
├── calibrate_cli.py              # 命令行校准工具
├── lottery_predictor.py          # 彩票预测（大乐透/排三/排五/七星彩）
├── lottery_fetcher.py            # 彩票数据抓取
├── main.py                       # CLI 主入口
├── run.py                        # Web 启动入口
├── requirements.txt              # Python 依赖
├── requirements-dev.txt          # 测试依赖
├── scripts/                       # 只读诊断工具
├── tests/                         # 单元、集成与固定样本
├── docs/                          # 优化路线图与实施说明
└── .gitignore
```

## 算法校准

项目的分阶段优化、测试门禁和模型准入规范见 [优化路线图](docs/optimization-roadmap.md)。

```bash
# 完整校准（抓取数据 → 回测 → 更新权重）
python calibrate.py

# 查看上次校准报告
python calibrate_cli.py --report

# 快速模式
python calibrate_cli.py --quick
```

校准流程：
1. 从 OpenLigaDB（德甲/德乙/德丙 2022-2024）和 500.com 抓取历史比赛
2. 用前 20% 数据初始化模型，后 80% 逐场滚动回测
3. 计算每个模型的 Brier Score 和 Log Loss
4. 根据回测结果重新分配融合权重

## 数据来源

| 来源 | 内容 | 说明 |
|------|------|------|
| OpenLigaDB | 德甲/德乙/德丙 2022-2024 | 免费 API，约 2000 场 |
| 500.com | 实时赔率 + 近期比赛 | HTML 解析 |
| 竞彩网 API | 实时赔率 + 比赛列表 | JSON 接口 |
| FIFA API | 世界杯比赛数据 | 赛后同步 |
| 内置 | 128 支球队 + 球员评级 | 国家队 + 俱乐部 |

## API 路由

| 路由 | 方法 | 说明 |
|------|------|------|
| `/` | GET | 预测主页 |
| `/predict` | POST | 运行候选模型并融合当前有效结果 |
| `/api/debug_predict` | POST | 查看完整计算过程 |
| `/api/search_matches` | GET | 搜索比赛/交锋/近期 |
| `/api/upcoming` | GET | 待开赛比赛列表 |
| `/api/refresh_data` | POST | 刷新线上数据（管理令牌） |
| `/api/status` | GET | 数据抓取状态 |
| `/api/progress/<id>` | SSE | 预测进度推送 |
| `/api/calibrate/run` | POST | 后台启动校准（管理令牌） |
| `/api/calibrate/status` | GET | 校准进度查询 |
| `/api/calibrate/report` | GET | 校准报告 |
| `/api/rankings` | GET | ELO 世界排名 |
| `/api/wc_matches` | GET | 世界杯赛果 |
| `/api/sync_fifa` | POST | 同步 FIFA 数据（管理令牌） |
| `/api/betting/analyze` | POST | 竞彩投注分析 |
| `/api/betting/matches` | GET | 投注比赛列表 |
| `/api/history/matches` | GET | 历史比赛（分页） |
| `/api/history/h2h` | GET | 两队交锋记录 |
| `/api/history/trend` | GET | 球队近期趋势 |
| `/api/lottery` | GET | 彩票开奖数据 |
| `/api/lottery` | POST | 强制刷新彩票开奖数据（管理令牌） |
| `/api/lottery/predict/<key>` | GET | 彩票预测分析 |
| `/api/lottery/predict/<key>` | POST | 重新生成彩票分析（管理令牌） |
| `/history` | GET | 历史记录页面 |
| `/betting` | GET | 竞彩投注页面 |
| `/calibrate` | GET | 校准页面 |
| `/lottery` | GET | 彩票页面 |

`/predict` 返回 `model_agreement`（模型一致度）、`model_summary`（独立模型可用数量）、`simulation`（融合后的蒙特卡洛派生分布）以及配置权重和本次实际权重。旧字段 `confidence`、`ensemble.weights` 和 `predictions.monte_carlo` 暂时保留兼容。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端框架 | Flask |
| 数值计算 | NumPy · SciPy |
| 机器学习 | XGBoost · scikit-learn |
| 前端 | 原生 JS · Chart.js（CDN） |
| 模型 | 泊松分布 · ELO · 蒙特卡洛 · 贝叶斯层次 |
| 校准指标 | Brier Score · Log Loss |

## License

MIT
