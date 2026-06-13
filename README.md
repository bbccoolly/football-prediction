# ⚽ 足球比赛预测系统

基于 **12 种算法** 的足球比赛预测系统，融合 ELO 评级、泊松分布、蒙特卡洛模拟、贝叶斯层次模型等，提供胜平负概率、让球胜负、比分预测、进球数、半全场等全面分析。

## 功能

- 🎯 **12 算法融合**：泊松分布、Dixon-Coles、ELO 评级、Massey 排名、近期状态、交锋记录、市场赔率、KNN 相似、XGBoost、神经网络、蒙特卡洛、贝叶斯层次
- 📊 **BMA 贝叶斯模型平均**：根据回测校准动态分配权重（2086场多联赛校准）
- 🏟️ **场地因素**：自动识别世界杯等中立场地，支持场地数据库
- 👥 **球员缺阵影响**：可勾选缺阵球员调整预测
- 🎰 **让球胜负预测**：±1 / ±1.5 / ±2 七个盘口概率
- 📈 **半全场预测**：半场/全场结果概率分布
- 📋 **近期比赛侧边栏**：一键点击直接预测
- 🔍 **计算过程透明**：可查看每个模型的公式和中间结果
- 🛠️ **命令行校准工具**：python calibrate_cli.py

## 快速部署

### 环境要求
- Python 3.10+
- pip

### 安装运行

`ash
# 1. 克隆仓库
git clone https://github.com/2891593122/football-prediction.git
cd football-prediction

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动服务
python run.py
`

访问 http://127.0.0.1:5000

### 算法校准（可选）

`ash
# 完整校准：抓取历史数据 → 回测 → 更新权重
python calibrate.py

# 查看校准报告
python calibrate_cli.py --report

# 快速模式（使用缓存数据）
python calibrate_cli.py --quick
`

## 项目结构

`
├── web/                   # Flask 前端
│   ├── app.py             # 主服务 & API
│   ├── templates/         # HTML 模板
│   └── static/            # CSS / JS / Chart.js
├── models/                # 12 个预测模型
│   ├── poisson.py         # 泊松分布（让球/半全场/比分）
│   ├── dixon_coles.py     # Dixon-Coles 模型
│   ├── elo.py             # ELO 评级系统
│   ├── massey.py          # Massey 排名
│   ├── form.py            # 近期状态
│   ├── head_to_head.py    # 历史交锋
│   ├── market_odds.py     # 市场赔率
│   ├── knn_similar.py     # KNN 相似比赛
│   ├── xgboost_model.py   # XGBoost
│   ├── neural_net.py      # 神经网络
│   ├── monte_carlo.py     # 蒙特卡洛模拟
│   └── bayesian_hierarchical.py  # 贝叶斯层次
├── ensemble/              # BMA 融合 + Stacking
├── features/              # 球员影响 + 特征构建
├── data/                  # 数据采集（OpenLigaDB + 500.com）
├── calibrate.py           # 回测校准主程序
├── calibrate_cli.py       # 命令行校准工具
├── config.py              # 全局配置（128队 + 球员数据）
├── run.py                 # 启动入口
└── requirements.txt       # Python 依赖
`

## 数据来源

- **OpenLigaDB**：德甲/德乙/德丙 2022-2024 赛季（免费 API）
- **500.com**：近期比赛赔率数据
- **内置数据**：128 支国家队 + 俱乐部球员评级

## 技术栈

- **后端**：Flask + NumPy + SciPy + XGBoost + scikit-learn
- **前端**：原生 JS + Chart.js（饼图/柱状图）
- **模型**：泊松分布 / ELO 评级 / 蒙特卡洛 / 贝叶斯层次
- **校准**：Brier Score + Log Loss 回测评估

## License

MIT