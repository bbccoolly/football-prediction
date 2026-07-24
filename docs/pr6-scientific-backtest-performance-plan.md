# PR 6：科研回测性能与断点恢复实施方案

> 状态：已规划，尚未实施。前置条件是 PR 5（`codex/data-coverage-governance`）已推送、通过 CI 并合入 `origin/master`。PR 6 必须从该合入后的 `master` 创建独立分支，建议分支名为 `codex/backtest-performance`。

## 1. 目标与边界

PR 6 将严格 Walk-forward 回测调整为可复现的科研执行模式：在不改变预测生产路径语义的前提下，将本机全量回测预期耗时从约 40 分钟降低至 15 分钟以内，并支持进程中断后的安全恢复。

本 PR 包含：

- 回测专用的轻量评分入口；
- 历史数据内存视图和按时间批次的运行时构建；
- 以批次为粒度的原子检查点与受限恢复；
- 可审计的运行规格、输入指纹和任务状态；
- CLI 及 Web 的科研模式入口和数据批次发现接口。

本 PR 不包含：

- 修改 `PredictionService.predict()` 的生产响应语义；
- 训练、启用或准入 XGBoost、神经网络、Stacking；
- 自动写入线上融合权重；
- Bootstrap 单次迭代级恢复；
- 修改彩票模块。

## 2. 性能依据与原则

对现有实现的只读剖析显示：一次完整预测约 0.97 秒，其中 Monte Carlo 约 0.43 秒、半全场计算约 0.37 秒、让球计算约 0.10 秒。它们均不参与 Walk-forward 的概率、对数损失、Brier 分数和校准指标。

因此性能优化采用以下原则：

1. 回测只调用共享服务的科研评分入口，仍保留独立模型和 BMA 融合概率。
2. 跳过仅面向展示的 Monte Carlo、半全场和让球派生计算，不重写或降低基础模型的统计标准。
3. 每个时间批次只读取一次历史数据，并从确定的 `as_of` 构建运行时。
4. 每次持久化都必须原子完成；未经完整校验的中间结果绝不参与报告或准入。

## 3. 公共接口与版本

### 3.1 预测服务

保留原接口：

```python
PredictionService.predict(request, include_trace=False, progress=None)
```

新增科研入口：

```python
PredictionService.evaluate(request, progress=None)
```

`evaluate()` 的行为：

- 使用与 `predict()` 相同的活动快照、特征、模型门禁、独立模型结果和 BMA 融合结果；
- 不执行 Monte Carlo、半全场、让球和其他仅展示用途的派生计算；
- 返回兼容的 `PredictionResult`，对跳过部分提供稳定的占位结构与明确 `status=not_evaluated`；
- 不写数据库、权重、模型产物或缓存；
- 不得绕过模型证据门禁、时间一致性验证和异常处理。

`BacktestRunner` 必须固定使用 `evaluate()`；生产 Web、CLI 和调试预测继续使用 `predict()`。

### 3.2 版本常量

新增或升级为：

```python
BACKTEST_SCHEMA_VERSION = 3
BACKTEST_PROTOCOL_VERSION = "walk_forward_v3_scientific_checkpoint"
```

报告、运行规格、检查点和恢复逻辑均须记录上述版本。V2 报告只读保留，不可作为 V3 的恢复来源。

## 4. 数据视图与无泄漏构建

### 4.1 内存历史视图

新增 `BacktestHistoryView`，由一次受限查询得到回测窗口需要的已完场比赛和必要球队/赛事元数据。其职责是：

- 按稳定排序提供 `as_of` 之前的历史切片；
- 保持同一精确开赛时间为一个批次；日期精度记录则将该自然日视为一个批次；
- 严格排除当前批次及未来数据；
- 不进行网络访问、数据库写入或隐式别名创建。

### 4.2 运行时构建

新增：

```python
ModelRuntimeBuilder.build_from_matches(matches, as_of)
```

它与现有 `build(as_of)` 使用相同的模型、门禁和快照协议，但直接消费 `BacktestHistoryView` 已过滤的内存比赛序列，避免每个批次重复 SQLite 查询。两种构建方式在相同输入下必须得到一致的可用模型状态、预测概率和确定性快照标识。

## 5. 检查点、运行规格与恢复

### 5.1 标识规则

V3 的阶段批次标识必须全局唯一，禁止 validation 和 holdout 同时使用 `batch-00001`：

```text
validation-batch-00001
holdout-batch-00001
```

检查点文件采用全局连续序号：

```text
checkpoint-00001.json
checkpoint-00002.json
...
```

文件仅位于被忽略的回测运行目录，禁止提交到 Git。

### 5.2 运行规格

每次新运行在开始前原子写入不可变 `run_spec.json`，至少包含：

- `run_id`、schema/protocol 版本、创建时间；
- 数据集批次 ID、训练/验证/保留集时间范围和 `as_of`；
- 回测参数、特征版本、模型门禁与权重来源；
- `code_commit`、`code_dirty`；
- `run_input_fingerprint`：规范化后的输入数据与范围指纹；
- `run_spec_fingerprint`：除时间戳外运行规格的规范 JSON SHA-256。

`run_input_fingerprint` 覆盖比赛数据、赛事/球队解析、赔率快照和运行范围；不得仅以比赛数量替代。

### 5.3 原子检查点

每完成一个时间批次后，先在临时文件写入完整 JSON、刷新并关闭文件，再以同目录原子替换写入正式检查点。检查点应包括：

- 当前阶段和批次 ID、全局序号；
- 已处理比赛数、排除统计、累计模型及融合指标；
- 当前输入/规格指纹；
- 批次输出摘要、状态和时间；
- 下一个可执行批次位置。

阶段完成后额外写入阶段级指标检查点。Bootstrap 只在阶段完成后执行，因此不支持单迭代恢复；中断时从最近已完成阶段或批次重新开始对应后续工作。

### 5.4 恢复规则

CLI 的 `--resume <run_id>` 和 Web 的 `resume_run_id` 仅允许恢复状态为：

- `interrupted`；
- `BACKTEST_PROCESS_LOST`；
- `BACKTEST_USER_INTERRUPTED`。

以下情况必须拒绝恢复并给出稳定错误码：

- 已完成运行；
- `run_input_fingerprint` 或 `run_spec_fingerprint` 不一致；
- 检查点损坏、缺失、重复、跳号或阶段顺序非法；
- 数据/配置校验失败，或运行目录不属于请求的 `run_id`。

恢复前必须完整验证已有检查点链，不能因为只存在最后一个文件就信任运行状态。

## 6. 科研模式与准入边界

CLI 新增：

```powershell
python calibrate_cli.py backtest --research-only ...
python calibrate_cli.py backtest --resume <run_id> ...
```

`--research-only` 明确禁止产生 `admitted` 结论、更新 BMA 权重或写入任何线上模型配置。数据不足仍返回 `insufficient_data`，候选模型仍为 `research_only`。

对正式数据库运行增加可追溯性门禁：

- `code_commit` 不得为 `"unknown"`；
- `code_dirty` 必须为 `false`；
- 未满足时拒绝正式运行，可在明确的 fixture/科研模式下执行，但报告必须带出警告。

## 7. Web 与任务接口

新增：

```text
GET /api/calibration/datasets
```

该接口只读列出可选择的数据批次、范围、比赛数、质量状态和可回测性，不泄露原始内部路径。

`POST /api/calibrate/run` 扩展支持：

- `dataset_batch_id`；
- `as_of`；
- `resume_run_id`；
- `research_only`。

接口继续遵循管理令牌保护。恢复请求不得同时携带会改变既有规格的范围、数据集或模型参数。后台任务状态增加阶段、当前批次、最近检查点、可恢复性和脱敏错误摘要；进程意外消失时将可恢复任务标记为 `BACKTEST_PROCESS_LOST`。

## 8. 实施顺序

1. 从 PR 5 合入后的 `master` 创建 `codex/backtest-performance`，记录干净工作区、基线测试和当前运行时性能基准。
2. 先编写失败测试：`evaluate()` 兼容性、生产路径未回归、数据视图隔离、V3 标识、检查点原子性、恢复拒绝规则与科研模式门禁。
3. 实现 `PredictionService.evaluate()`，使其复用预测服务核心计算但跳过派生展示计算。
4. 实现 `BacktestHistoryView` 与 `build_from_matches()`，验证与数据库构建的一致性和无泄漏批次边界。
5. 将 `BacktestRunner` 迁移到科研评分入口，升级报告为 schema V3，并实现批次检查点、`run_spec.json` 和恢复校验。
6. 扩展 CLI、后台任务和 Web API，保持原有认证、状态码与响应兼容性。
7. 执行小型 fixture、可控中断/恢复和全量本地回测基准；仅在性能及结果一致性均达标时更新文档。

建议提交拆分：

```text
test: 固化科研回测与检查点恢复契约
refactor: 增加回测内存历史视图与轻量评分入口
feat: 支持回测批次检查点和受限恢复
feat: 增加科研回测数据集与任务接口
docs: 记录科研回测性能与恢复流程
```

## 9. 验收标准

- `predict()` 的现有响应与生产派生计算保持兼容；`evaluate()` 不执行 Monte Carlo、半全场或让球。
- 在相同 fixture、时间范围和配置下，V3 回测概率、指标、排除统计及指纹可复现。
- 任意批次中断后，合法恢复结果与不中断连续执行结果一致；非法或不完整检查点绝不恢复。
- Validation 与 holdout 批次标识不冲突，检查点序号连续且原子写入。
- 回测训练与评分过程无未来数据泄漏，不重复查询数据库构建等价快照。
- 全量本机回测在目标数据规模下不超过 15 分钟，并记录机器、数据规模、运行规格和测量结果；性能目标未达成时不得以降低统计标准伪造达标。
- 正式数据库回测拒绝未知提交或脏工作区；科研模式不产生准入和权重写入。
- Web 数据集发现、启动、恢复和状态查询均经过认证及输入校验。
- 执行 `pytest -q`、`python -m compileall -q .`、`git diff --check`；涉及页面操作时完成桌面与移动端浏览器验收。

## 10. 风险与回滚

- 仅新增 V3 运行目录和协议，不修改或删除既有 V2 报告；出现问题时可停止创建 V3 运行，不影响生产预测。
- 检查点和报告属于运行时数据，必须由 `.gitignore` 排除；测试使用临时目录。
- 运行时构建失败、输入指纹不一致或恢复校验失败时不得覆盖已有检查点或报告。
- 本 PR 不改变模型准入结论。数据覆盖和样本质量不足时，保持 `research_only` 或 `insufficient_data` 是预期且正确的结果。
