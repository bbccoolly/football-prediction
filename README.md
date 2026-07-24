# ⚽ 足球比赛预测系统

基于 **11 个独立候选模型** 的足球比赛预测系统，融合泊松分布、ELO 评级、贝叶斯层次模型等，覆盖胜平负、让球胜负、比分、进球数、半全场等预测。蒙特卡洛只对最终融合结果进行派生采样；未训练、无真实输入或输出非法的模型不会参与最终融合。

## 功能特性

| 功能 | 说明 |
|------|------|
| 🎯 11 个独立候选模型 | 泊松 / Dixon-Coles / ELO / Massey / 近期状态 / 交锋记录 / 市场赔率 / KNN / XGBoost / 神经网络 / 贝叶斯层次；蒙特卡洛作为融合后的派生模拟 |
| 📊 受控融合 | PR 3 使用代码内置可信权重，并按本次有效模型重新归一化；旧校准权重不进入活动快照 |
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

刷新数据、FIFA 同步、后台回测和彩票强制刷新需要管理令牌：

```powershell
$env:FOOTBALL_ADMIN_TOKEN = "your-secret-token"
python run.py
```

默认只监听 `127.0.0.1:5000`。可通过 `FOOTBALL_HOST` 和 `FOOTBALL_PORT` 修改；监听非回环地址时必须配置管理令牌。

历史比赛读取不会隐式联网或创建数据库：存在 SQLite 标准库时优先读取，否则只读回退旧 JSON。启动时只读加载本地待开赛缓存，即使缓存过期也不会自动抓取；线上刷新和数据同步只能通过受管理令牌保护的显式接口执行。

### 历史数据迁移

首次启用标准比赛库前先执行 dry-run，命令只输出导入、重复、拒绝和未匹配统计，不创建数据库：

```bash
python scripts/migrate_history.py --source data/processed/match_history.json
```

确认报告后显式写入 SQLite：

```bash
python scripts/migrate_history.py --source data/processed/match_history.json --apply
```

默认数据库为 `data/processed/football.db`，可使用 `FOOTBALL_DB_PATH` 或 `--database PATH` 覆盖。运行时数据库和旧历史数据均被 Git 忽略，不应提交。

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
│   ├── bma.py                    #   融合实现（运行时使用内置可信权重）
│   ├── prediction_contract.py    #   模型可用性与概率协议
│   └── stacker.py                #   Stacking 元学习器
│
├── features/                     # 特征工程
│   ├── builder.py                #   18 维特征向量构造器
│   └── player_impact.py          #   球员影响力评估
│
├── data/                         # 数据层
│   ├── fetcher.py                #   500.com / 竞彩网实时抓取
│   ├── match_repository.py       #   SQLite 标准比赛仓库
│   ├── source_adapters.py        #   OpenLigaDB / 500.com / FIFA 来源适配
│   ├── fifa_sync.py              #   FIFA 显式同步
│   ├── history_db.py             #   SQLite 优先、旧 JSON 只读兼容
│   ├── reference/                #   受版本控制的球队别名种子
│   └── venue_db.py               #   场地数据库
│
├── prediction/                   # 共享预测运行时
│   ├── contracts.py              #   请求、快照、结果和稳定异常契约
│   ├── artifacts.py              #   只读模型产物门禁
│   ├── runtime.py                #   分域模型快照与原子刷新
│   └── service.py                #   Web / CLI 共用预测入口
│
├── backtest/                     # 可信 Walk-forward 回测
│   ├── runner.py                 #   严格时间批次执行器
│   ├── metrics.py                #   配对指标与分块 Bootstrap
│   ├── admission.py              #   模型准入门禁
│   └── tasks.py                  #   后台任务状态与恢复
│
├── betting/                      # 竞彩投注
│   ├── jczq_engine.py            #   投注分析引擎
│   ├── jczq_planner.py           #   投注方案规划
│   ├── jczq_fetcher.py           #   赔率实时抓取
│   └── jczq_team_db.py           #   球队数据
│
├── config.py                     # 全局配置（15 联赛 · 128 球队 · 算法参数）
├── calibrate.py                  # 新回测 CLI 的弃用兼容入口
├── calibrate_cli.py              # 离线回测、报告与准入命令行
├── lottery_predictor.py          # 彩票预测（大乐透/排三/排五/七星彩）
├── lottery_fetcher.py            # 彩票数据抓取
├── main.py                       # CLI 主入口
├── run.py                        # Web 启动入口
├── requirements.txt              # Python 依赖
├── requirements-dev.txt          # 测试依赖
├── scripts/                       # 诊断与显式迁移工具
│   └── migrate_history.py         #   历史 JSON dry-run / 正式迁移
├── tests/                         # 单元、集成与固定样本
├── docs/                          # 优化路线图与实施说明
└── .gitignore
```

## 回测与模型准入

项目的分阶段优化、测试门禁和模型准入规范见 [优化路线图](docs/optimization-roadmap.md)。

PR 3 冻结使用 `INITIAL_WEIGHTS`，快照会报告 `weights_source=builtin_v1` 和权重指纹。旧 `ensemble/weights.json`、旧校准报告及旧 pickle 产物不会被运行时加载。

可信回测严格按自然日和开赛时间分批，只使用批次开始前的数据。默认输出到被 Git 忽略的 `data/processed/backtests/<run_id>/`：

```powershell
python calibrate_cli.py backtest --database data/processed/football.db
python calibrate_cli.py backtest --fixture tests/fixtures/backtest_matches.json --allow-insufficient-data --as-of 2027-01-01T00:00:00Z
python calibrate_cli.py report --run-id <run_id>
python calibrate_cli.py admission --run-id <run_id>
```

退出码 `0` 表示完成，`1` 表示配置或执行失败，`2` 表示报告已完成但正式数据门禁不足。当前本地历史规模低于 1500 场，正常结果应是 `research_only` 或 `insufficient_data`，不能据此启用模型。

回测不会联网、训练学习模型或写入线上权重。XGBoost、神经网络和 Stacking 在本阶段统一为 `not_evaluated`；任何线上权重调整仍需独立审查。

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
| `/api/status` | GET | 数据、活动快照、模型状态和刷新状态 |
| `/api/progress/<id>` | SSE | 预测进度推送 |
| `/api/calibration` | GET | 最近一次已完成的回测报告 |
| `/api/calibrate/run` | POST | 启动后台 Walk-forward 回测（管理令牌） |
| `/api/calibrate/status` | GET | 按 run ID 查询持久化任务状态 |
| `/api/calibrate/report` | GET | 按 run ID 查询指标和准入报告 |
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
| `/calibrate` | GET | 回测与模型准入页面 |
| `/lottery` | GET | 彩票页面 |

`/predict` 返回 `model_agreement`（模型一致度）、`model_summary`（独立模型可用数量）、`simulation`（融合后的蒙特卡洛派生分布）、证据排除原因和训练数据质量；同时返回 `prediction_run_id`、运行时快照 ID、数据/配置/权重指纹、特征版本及模型训练元数据。旧字段 `confidence`、`ensemble.weights` 和 `predictions.monte_carlo` 暂时保留兼容。

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
