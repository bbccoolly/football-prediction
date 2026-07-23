# 项目协作指南

## 沟通语言

- Agent 默认使用中文进行分析、进度同步、问题说明和结果总结。
- 代码标识符、命令、路径和第三方技术名称保留原文，避免不必要的翻译。
- 面向用户的新增文案应与现有中文界面保持一致；只有明确要求国际化时才新增其他语言。

## Git 代码管理

- Git 提交信息使用中文，准确说明变更目的，避免“更新”“修改”等模糊描述。
- 变更摘要、代码审查意见、Pull Request 标题和说明默认使用中文。
- 分支名使用兼容 Git 和自动化工具的 ASCII 格式；Codex 创建的分支使用 `codex/` 前缀。
- 提交前检查 `git diff` 和 `git status`，不得混入 `.idea/`、`.workbuddy/`、运行时数据或模型产物。
- 未经用户明确要求，不执行强制推送、硬重置、覆盖分支或删除远程分支等破坏性操作。
- Fork 工作流中，`origin` 指向个人仓库，`upstream` 仅用于同步原仓库。

## 项目用途

本项目是基于 Python 和 Flask 的足球预测与竞彩分析应用，同时包含独立的彩票统计模块。修改业务逻辑时，应保持足球预测与彩票模块相互隔离。

## 环境与命令

- 使用 Python 3.10 或更高版本。
- 安装依赖：`python -m pip install -r requirements.txt`。
- 安装开发依赖：`python -m pip install -r requirements-dev.txt`。
- 启动 Web 应用：`python run.py`。
- 启动交互式命令行：`python main.py`。
- 执行自动化测试：`pytest -q`。测试默认禁止网络访问。
- 执行编译检查：`python -m compileall -q .`。
- 只有在允许联网和写入生成数据时，才运行校准：`python calibrate.py`。

## 项目结构

- `web/`：Flask 路由、模板和静态资源。
- `models/`：各类预测模型。
- `features/`：特征构建和球员影响逻辑。
- `ensemble/`：BMA 和 Stacking 集成实现。
- `data/`：外部数据采集和本地历史数据。
- `betting/`：独立的竞彩足球分析与方案规划流程。
- `config.py`：联赛、球队、模型参数和路径配置。

## 仓库边界

- `data/raw/`、`data/processed/` 和 `data/lottery_cache/` 中的运行时数据不得纳入版本控制。
- `ensemble/saved_models/` 和 `ensemble/weights.json` 中的模型与校准产物不得纳入版本控制。
- `.idea/` 和 `.workbuddy/` 是本地工具状态，不得纳入版本控制。
- 不能仅因模型模块存在就宣称模型已启用；必须检查训练状态和模型文件。
- 预测、校准和模型融合中的模型标识必须保持一致。

## 验证要求

修改后至少运行 `pytest -q`、`python -m compileall -q .` 和对应的定向冒烟检查。涉及页面交互时还需完成浏览器验收。只读分析期间不要触发模型初始化，因为初始化可能抓取数据并更新本地 ELO 或模型产物。

## 深入文档

- `docs/optimization-roadmap.md`：后续阶段、模型准入和完成定义。
- `docs/iteration-a-implementation-plan.md`：预测正确性基座的接口、迁移和验收细节。
