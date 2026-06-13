# ⚽ 足球比赛预测系统

基于 **12 种算法** 的足球比赛预测系统，融合 ELO 评级、泊松分布、蒙特卡洛模拟、贝叶斯层次模型等，提供胜平负概率、比分预测、进球数、半全场等全面分析。

## 功能

- 🎯 **12 算法融合**：泊松分布、Dixon-Coles、ELO 评级、Massey 排名、近期状态、交锋记录、市场赔率、KNN 相似、XGBoost、神经网络、蒙特卡洛、贝叶斯层次
- 📊 **BMA 贝叶斯模型平均**：根据回测校准动态分配权重
- 🏟️ **场地因素**：自动识别世界杯等中立场地
- 👥 **球员缺阵影响**：可勾选缺阵球员调整预测
- 📈 **半全场预测**：半场/全场结果概率分布`n- 🎰 **让球胜负预测**：±1 / ±1.5 / ±2 七个盘口概率`n- 📋 **近期比赛侧边栏**：半场/全场结果概率分布
- 📋 **近期比赛侧边栏**：一键点击直接预测
- 🔍 **计算过程透明**：可查看每个模型的公式和中间结果
- 🛠️ **命令行校准工具**：python calibrate_cli.py

## 快速开始

`ash
pip install -r requirements.txt
python run.py
`

访问 http://127.0.0.1:5000

## 项目结构

`
├── web/               # Flask 前端
│   ├── app.py         # 主服务
│   ├── templates/     # HTML 模板
│   └── static/        # CSS/JS
├── models/            # 12 个预测模型
├── ensemble/          # BMA 融合 + Stacking
├── features/          # 球员影响 + 特征构建
├── data/              # 数据采集与历史库
├── calibrate.py       # 回测校准
├── calibrate_cli.py   # 命令行校准工具
├── config.py          # 全局配置
└── run.py             # 启动入口
`

## 算法校准

`ash
# 完整校准（抓取数据+回测+更新权重）
python calibrate.py

# 命令行工具
python calibrate_cli.py --quick    # 快速模式
python calibrate_cli.py --report   # 查看报告
`

## 数据来源

- OpenLigaDB（德甲历史数据）
- 500.com（近期比赛）
- 内置 128 支国家队+俱乐部球员数据