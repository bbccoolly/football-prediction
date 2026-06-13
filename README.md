# ⚽ 足球比赛预测系统

基于 **12 种算法** 的足球比赛预测系统，融合 ELO 评级、泊松分布、蒙特卡洛模拟、贝叶斯层次模型等，覆盖胜平负、让球胜负、比分、进球数、半全场等全面预测。

## 功能特性

| 功能 | 说明 |
|------|------|
| 🎯 12算法融合 | 泊松分布 · Dixon-Coles · ELO · Massey · 近期状态 · 交锋记录 · 市场赔率 · KNN · XGBoost · 神经网络 · 蒙特卡洛 · 贝叶斯 |
| 📊 BMA融合 | 贝叶斯模型平均，2086场多联赛回测校准动态权重 |
| 🎰 让球胜负 | ±1 / ±1.5 / ±2 七个盘口概率 |
| 📈 半全场 | 半场/全场组合概率分布 |
| 🏟️ 场地因素 | 自动识别世界杯等中立场地 |
| 👥 球员缺阵 | 勾选排除缺阵球员调整预测 |
| 📋 侧边栏 | 近期比赛一键点击直接预测 |
| 🔍 计算过程 | 查看每模型公式、中间结果、融合权重 |

## 快速开始

```bash
git clone https://github.com/2891593122/football-prediction.git
cd football-prediction
pip install -r requirements.txt
python run.py
```

浏览器打开 **http://127.0.0.1:5000**

## 项目结构

```
football-prediction/
│
├── web/                          # Flask 前端
│   ├── app.py                    #   主服务 · 全部 API
│   ├── templates/
│   │   ├── index.html            #   预测主页
│   │   └── history.html          #   历史记录
│   └── static/
│       ├── css/style.css         #   深色主题 UI
│       └── js/app.js             #   前端交互逻辑
│
├── models/                       # 12 个预测模型
│   ├── poisson.py                #   泊松分布（让球·半全场·比分）
│   ├── dixon_coles.py            #   Dixon-Coles 低分修正
│   ├── elo.py                    #   ELO 评级系统
│   ├── massey.py                 #   Massey 排名
│   ├── form.py                   #   近期状态
│   ├── head_to_head.py           #   历史交锋
│   ├── market_odds.py            #   市场赔率先验
│   ├── knn_similar.py            #   KNN 相似比赛
│   ├── xgboost_model.py          #   XGBoost
│   ├── neural_net.py             #   神经网络
│   ├── monte_carlo.py            #   蒙特卡洛模拟
│   └── bayesian_hierarchical.py  #   贝叶斯层次模型
│
├── ensemble/                     # 融合层
│   ├── bma.py                    #   BMA 贝叶斯模型平均
│   └── stacker.py                #   Stacking 集成
│
├── features/                     # 特征工程
│   ├── builder.py                #   特征构建器
│   └── player_impact.py          #   球员影响力
│
├── data/                         # 数据层
│   ├── fetcher.py                #   500.com / OpenLigaDB 抓取
│   ├── history_db.py             #   历史数据库
│   └── venue_db.py               #   场地数据库
│
├── calibrate.py                  # 回测校准主程序
├── calibrate_cli.py              # 命令行校准工具
├── config.py                     # 全局配置（128队 + 球员数据）
├── run.py                        # 启动入口
└── requirements.txt              # Python 依赖
```

## 算法校准

```bash
# 完整校准（抓取数据 → 回测 → 更新权重）
python calibrate.py

# 查看上次报告
python calibrate_cli.py --report

# 快速模式
python calibrate_cli.py --quick
```

## 数据来源

| 来源 | 内容 | 说明 |
|------|------|------|
| OpenLigaDB | 德甲/德乙/德丙 2022-2024 | 免费 API，~2000 场 |
| 500.com | 近期比赛赔率 | 实时抓取 |
| 内置 | 128 支球队 + 球员评级 | 国家队 + 俱乐部 |

## 技术栈

**后端** Flask · NumPy · SciPy · XGBoost · scikit-learn
**前端** 原生 JS · Chart.js（饼图/柱状图）
**模型** 泊松分布 · ELO · 蒙特卡洛 · 贝叶斯层次
**校准** Brier Score · Log Loss 滚动回测

## License

MIT
