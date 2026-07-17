# 足球预测系统

12种算法融合的足球比赛预测系统。

## 启动
```bash
python run.py
```
浏览器打开 http://127.0.0.1:5000

## 项目结构
- `web/app.py` — Flask主服务，所有API路由
- `web/templates/` — HTML页面（index/history/bet_plan/calibrate/lottery）
- `web/static/js/app.js` — 前端逻辑
- `models/` — 12个预测模型
- `ensemble/bma.py` — BMA融合
- `calibrate.py` — 回测校准
- `config.py` — 全局配置（球队/球员/参数）
- `data/` — 数据抓取+历史库
- `betting/` — 竞彩投注引擎
- `features/` — 特征工程
- `lottery_predictor.py` — 彩票预测

## 关键文件
- 球队/球员数据: `config.py`
- 校准权重: `ensemble/weights.json`
- 回测报告: `data/processed/calibration_report.json`
- ELO评分: `data/processed/elo_ratings.json`
- 历史比赛: `data/processed/match_history.json`
