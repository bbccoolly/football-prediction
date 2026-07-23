# 迭代 B：预测收口、数据治理与可信回测实施方案

> 实施状态：PR 1 已于 2026-07-23 在 `codex/prediction-hardening` 完成实现、本地验证和提交（`fdb535d`）；PR 2 已于 2026-07-23 在 `codex/match-repository` 完成实现和本地验证，等待代码审查与提交；PR 3 尚未开始。本文档是迭代 B 的决策完整方案，实施时按连续、可独立审查的 PR 交付。

## 1. 实施目标

本阶段承接已完成的迭代 A，按以下顺序完成下一阶段建设：

1. 收口模型语义、写接口和运行配置中的已知问题。
2. 引入 SQLite 标准比赛库，并兼容迁移现有 JSON 历史数据。
3. 抽取 Web、CLI、调试接口和回测共用的预测服务。
4. 建立严格、无未来数据泄漏的 Walk-forward 回测。
5. 根据冻结保留集生成研究级模型准入清单。

本阶段不自动训练或启用 XGBoost、神经网络和 Stacking，不自动覆盖线上融合权重，也不修改彩票模块的数据结构。

## 2. 交付策略

PR 1 已从迭代 A 完成提交开始实施；剩余 PR 从前一个已验证提交创建连续分支，不在一个 PR 中混合全部重构：

| PR | 分支 | 交付目标 |
|---|---|---|
| PR 1 | `codex/prediction-hardening` | 模型语义、写接口、安全配置和 CI 收口 |
| PR 2 | `codex/match-repository` | SQLite、球队别名、幂等导入和 JSON 迁移 |
| PR 3 | `codex/prediction-service` | 共享预测服务、运行时快照和原子刷新 |
| PR 4 | `codex/walk-forward-backtest` | 严格回测、指标报告和研究级准入清单 |

三个剩余 PR 是串行依赖关系，必须按 PR 2、PR 3、PR 4 的顺序审查和合并。每个 PR 独立运行测试并使用中文提交说明。运行时数据库、历史数据、报告、权重和模型产物不得提交。

## 3. PR 1：预测与接口收口

### 3.1 建立失败测试

增加以下回归测试：

- Massey 裁剪、舍入后概率和仍为 1。
- 无真实赔率时市场模型不可用且有效权重为 0。
- 蒙特卡洛永远不作为独立模型进入 BMA。
- 权重文件中的旧蒙特卡洛权重迁移为 0。
- 最近 14 天日期跨月、跨年时仍合法。
- 所有写接口拒绝 GET、缺少令牌和错误令牌。
- 非回环地址启动时缺少管理令牌应失败。
- 现有 `/predict` 兼容字段保持可用。

推荐提交：

```text
test: 增加预测收口与写接口回归测试
```

### 3.2 修复模型职责

#### Massey

1. 计算未裁剪的三项概率。
2. 将每项限制在 `[0.01, 0.98]`。
3. 对三项重新归一化。
4. 舍入后再次归一化，最终通过公共预测协议。

#### 市场赔率

- 只有完整手工赔率或真实赔率快照才能返回 `available=true`。
- 无赔率返回：
  - `status=no_market_odds`
  - `available=false`
  - `warnings=["market_odds_missing"]`
- 固定 `45/28/27` 从市场模型中删除，后续作为独立赛事比例基线实现。
- 无效、非有限或小于等于 1 的赔率统一拒绝。

#### 蒙特卡洛

- BMA 只接收独立基础模型。
- 先生成最终融合概率，再由蒙特卡洛采样比分和总进球。
- API 新增顶层 `simulation`。
- 暂时保留 `predictions.monte_carlo`，但标记：
  - `role=derived`
  - `status=derived`
  - `available=false`
- `effective_weights.monte_carlo` 固定为 0。
- 权重 schema 升级并将已有蒙特卡洛权重迁移为 0。
- 有效模型数量只统计独立基础模型。

推荐提交：

```text
fix: 修正基础模型与蒙特卡洛融合职责
```

### 3.3 修复日期生成

- 使用注入时钟计算最近 14 个完整自然日，不包含当天。
- 统一输出 `YYYY-MM-DD`。
- 删除 `history_db.py` 中硬编码的 2026 年日期。
- 抓取结果区分：
  - `success`
  - `no_matches`
  - `request_failed`
  - `parse_failed`
- 单元测试覆盖闰年、跨月、跨年和夏令时日期。

推荐提交：

```text
fix: 动态生成历史抓取日期并区分失败状态
```

### 3.4 收紧写接口

以下操作统一改为 POST 并校验 `Authorization: Bearer <token>`：

- `/api/refresh_data`
- `/api/sync_fifa`
- `/api/calibrate/run`
- 彩票强制刷新。
- 彩票重新生成或更新缓存。

执行规则：

- 令牌来自 `FOOTBALL_ADMIN_TOKEN`。
- 未配置返回 `503 ADMIN_TOKEN_NOT_CONFIGURED`。
- 缺少或错误令牌返回 `401 ADMIN_AUTH_REQUIRED`。
- GET 调用返回 405，不再通过查询参数触发写入。
- 页面令牌只保存在当前页面 JavaScript 内存；不写入 URL、Cookie、HTML、日志、`localStorage` 或 `sessionStorage`。
- 页面刷新后重新输入；401 后立即清除内存令牌。
- 普通只读历史、预测和彩票查询不要求管理令牌。

`run.py` 调整为：

- 默认 `FOOTBALL_HOST=127.0.0.1`。
- 默认 `FOOTBALL_PORT=5000`。
- 非回环地址启动时必须设置管理令牌，否则直接退出。
- 不在启动日志中打印令牌。

推荐提交：

```text
fix: 保护写接口并收紧服务监听配置
```

### 3.5 建立 CI

新增 GitHub Actions，在 Python 3.10 和 3.12 上执行：

```powershell
python -m compileall -q .
pytest -q
```

网络在测试中保持禁用。失败测试不得通过重试掩盖。

推荐提交：

```text
ci: 增加 Python 编译与离线测试门禁
```

## 4. PR 2：标准比赛仓库与迁移

### 4.1 SQLite Schema

使用标准库 `sqlite3`，默认数据库为 `data/processed/football.db`，支持 `FOOTBALL_DB_PATH` 覆盖。使用 `PRAGMA user_version` 管理迁移。

核心表如下。

#### `teams`

- `team_id`
- `canonical_name`
- `team_type=national|club`
- `country_code`
- 创建和更新时间

#### `team_aliases`

- `source`
- `raw_alias`
- `normalized_alias`
- `team_type`
- `team_id`
- 唯一键 `(source, normalized_alias, team_type)`

#### `competitions`

- `competition_id`
- 标准名、类型、国家和级别

#### `matches`

- `match_id`
- `competition_id`
- 赛季、阶段
- `event_date`
- 可空 `kickoff_utc`
- `source_timezone`
- `time_precision=date|minute`
- 主客队 ID
- `neutral`
- `status=scheduled|finished|postponed|cancelled`
- 可空比分
- 数据质量状态

#### `source_records`

- 来源和源站记录 ID
- 原始载荷 JSON 及载荷指纹
- 可空身份指纹和来源修订时间
- 抓取时间
- 解析状态和错误摘要
- 唯一键 `(source, source_record_id, payload_fingerprint)`；无来源 ID 时使用 `(source, identity_fingerprint, payload_fingerprint)` 保证重复同步幂等

#### `match_sources`

- 关联标准比赛与来源记录。
- 每个来源记录最多关联一场标准比赛。

#### `sync_runs`

- 同步类型、范围、开始和结束时间
- 抓取、导入、跳过和失败数量
- 状态和错误摘要

#### `unmatched_team_aliases`

- 来源、原始名称、推测类型
- 首次和最近出现时间、出现次数和处理状态

#### `duplicate_candidates`

- 两个疑似比赛 ID、相似原因和审核状态

#### `odds_snapshots`

- 比赛、公司、采集时间
- 胜平负赔率和来源
- 唯一键 `(match_id, company, captured_at)`

数据库启用外键、唯一约束和事务。

### 4.2 稳定身份与更新规则

- 有可靠源站 ID：`source + source_record_id` 决定来源身份，改期和比分修正不改变标准比赛 ID。
- 无源站 ID：
  - 身份指纹使用赛事、赛季、主客队和原始日期。
  - 内容指纹包含时间、状态和比分。
- 时间或比分变化更新内容，不创建新比赛。
- 无源 ID 且发生改期时进入 `duplicate_candidates`，不自动合并。
- 同一天、同赛事、同球队的疑似重复记录进入审核队列。
- 原始来源记录按修订只追加和审计，不覆盖历史载荷。
- 完全相同的载荷重复导入不追加来源修订，计入 `skipped`。
- 同一可靠来源 ID 出现新载荷时追加来源修订，并更新已关联比赛的时间、状态或比分。
- `import_source_records()` 返回 `inserted`、`updated`、`skipped`、`rejected` 和 `unmatched` 计数，写入对应 `sync_runs`。

### 4.3 时间精度与防泄漏规则

不得把日期伪造成精确时间：

- 有可靠时间戳时保存 `kickoff_utc` 和 `time_precision=minute`。
- 只有日期时保存 `event_date`、`kickoff_utc=NULL`、`time_precision=date`。
- 保留原始时区和原始时间字符串。
- 同一精确开赛时间的一组比赛全部预测完成后再更新历史。
- 日期级比赛按整日批次处理：当天所有比赛预测完成后才能加入模型历史。
- 无日期、未来赛果或非法比分不得进入训练集。

### 4.4 球队别名治理

新增受 Git 管理的 `data/reference/team_aliases.json`：

- 初始覆盖现有德甲英文名、页面中文名和国家队名称。
- 自动规范化仅处理 Unicode、首尾空白、连续空格和英文大小写。
- 不使用编辑距离自动合并。
- 国家队和俱乐部必须明确指定类型。
- 未匹配来源记录保存在 `source_records` 和未匹配队列中，但不创建 `matches` 训练记录。
- 新别名经人工加入种子文件后，可运行重解析命令。

### 4.5 来源适配与统一写入口

OpenLigaDB、500.com 和 FIFA 各自实现来源适配器，统一输出 `SourceRecord`：

```text
source
source_record_id
fetched_at
raw_payload
competition
season
stage
event_date
kickoff_utc
source_timezone
time_precision
home_team_raw
away_team_raw
team_type
neutral
status
home_goals
away_goals
```

- 适配器只负责解析和保留原始值，不直接写数据库、不创建球队、不更新模型。
- OpenLigaDB 使用源站比赛 ID；FIFA 使用官方比赛 ID；500.com 在缺少可靠 ID 时生成身份指纹。
- 抓取成功但没有比赛、请求失败、解析失败和部分记录拒绝必须分别记录。
- 历史刷新、FIFA 同步和 JSON 迁移只能调用 `import_source_records()`，禁止路由继续直接调用 `add_match()`。
- 数据库尚未初始化时，写接口返回稳定配置错误，不得隐式创建数据库或回退写 JSON。

### 4.6 Repository 接口

实现 `MatchRepository`：

```text
import_source_records(records, sync_run_id)
list_matches(filters, as_of=None)
get_training_matches(before, competition_id=None)
resolve_team(raw_name, source, team_type)
list_unmatched_aliases()
reprocess_unmatched()
save_odds_snapshot(snapshot)
get_pre_match_odds(match_id, before)
build_data_fingerprint(filters)
```

所有批量导入使用单一事务；关键记录失败时整批回滚。Repository 查询和导入使用显式初始化入口；数据库文件存在但 `user_version` 不受支持、Schema 缺失或损坏时立即报错，不得静默回退 JSON。

### 4.7 JSON 迁移

提供：

```powershell
python scripts/migrate_history.py --source data/processed/match_history.json
python scripts/migrate_history.py --source PATH --database PATH --apply
```

默认 dry-run：

- 不创建或修改数据库。
- 输出合法、重复、拒绝、未匹配和赛事分布。
- 旧 JSON 来源统一标记为 `legacy_json`。
- 只有日期的记录保持日期精度。
- 无法确认球队类型的记录进入未匹配队列。

兼容期：

- 数据库存在时优先读取 SQLite。
- 数据库不存在时允许旧 JSON 只读回退并记录弃用警告。
- 所有新增写入只进入 SQLite。
- 应用读取历史数据不得自动联网或隐式创建数据库。
- 删除 JSON 回退属于后续独立迁移，不以“稳定一段时间”等模糊条件自动执行。

`history_db.load_history()` 在兼容期返回以下固定结构，避免 PR 2 提前破坏现有模型：

```text
match_id
home_team_id / away_team_id
home_team / away_team
competition_id / league
event_date / date
kickoff_utc / time_precision
neutral / status
home_goals / away_goals
```

其中 `home_team`、`away_team` 和 `league` 返回规范中文展示名；PR 3 内部预测再切换到稳定 ID。兼容适配器只读，不得承载新增写入。

推荐提交顺序：

```text
feat: 建立标准比赛数据库结构
feat: 增加球队别名与幂等导入
feat: 迁移历史 JSON 并保留只读兼容
```

## 5. PR 3：共享预测服务与原子刷新

### 5.1 公共接口

定义：

```text
PredictionRequest
ModelArtifactMetadata
ModelRuntimeSnapshot
PredictionResult
PredictionService
ModelRuntimeBuilder
```

`PredictionRequest` 包含：

- 标准球队 ID
- 赛事 ID
- 预测时点
- 中立场
- 可选赔率快照
- 缺阵信息

`ModelRuntimeSnapshot` 包含：

- 快照 ID
- 数据指纹
- 构建时间
- 训练截止时间
- 特征版本
- 模型实例和模型元数据

`PredictionResult` 包含：

- 单模型结果
- 最终融合
- 派生模拟
- 有效权重和排除原因
- 数据质量
- `prediction_run_id`
- 快照与模型版本信息

### 5.2 模型产物元数据

任何可加载产物必须带有：

```json
{
  "artifact_schema_version": 1,
  "model_id": "xgboost",
  "model_version": "1",
  "feature_version": "2",
  "trained_from": "ISO-8601",
  "trained_until": "ISO-8601",
  "training_sample_count": 0,
  "training_data_fingerprint": "sha256",
  "parameter_fingerprint": "sha256",
  "code_commit": "git-sha"
}
```

加载条件：

- 模型 ID 一致。
- 特征版本和维度一致。
- 参数指纹兼容。
- 回测时 `trained_until` 必须早于预测时间。
- 不满足条件返回明确不可用状态，不尝试猜测兼容。
- 本阶段不训练或启用 XGBoost、神经网络和 Stacking。

### 5.3 统一特征与预测路径

- Web、CLI、调试和回测全部调用 `PredictionService.predict()`。
- `FeatureBuilder` 填充真实 `massey_diff`。
- 特征名称、顺序和版本固定。
- 所有数据查询带 `as_of`，禁止读取预测时点之后的数据。
- 缺失值、中立场、球队未知和赔率处理只有一套规则。
- 单模型出现已定义的不可用状态时只排除对应模型；未捕获异常或非法模型状态按快照构建失败处理。
- 无可用模型统一抛出 `NoAvailableModelsError`。

兼容字段在迭代 B 内不得删除，并标记为 deprecated：

- `confidence`
- `ensemble.weights`
- `predictions.monte_carlo`

同时新增：

- `simulation`
- `prediction_run_id`
- `data_fingerprint`
- `feature_version`
- `runtime_snapshot_id`
- 模型训练范围和样本量

兼容字段只允许在后续主 API 版本的独立 PR 中删除；删除前必须更新页面调用、README 和契约测试。

### 5.4 原子构建与刷新

- 应用启动只从 Repository 和合法产物构建快照，不自动抓取或训练。
- `ModelRuntimeBuilder` 在局部对象中完整构建所有历史依赖模型。
- Repository 查询、数据指纹或共享特征构建失败时，整个快照构建失败。
- 单模型因样本不足、不适用或产物缺失而不可用时，快照记录明确的 `unavailable` 状态并允许继续构建。
- 单模型出现未捕获异常、非法概率或不一致状态时，整个快照构建失败。
- 新快照至少包含一个通过公共协议校验的独立基础模型；满足条件后才在锁内一次替换活动快照。
- 构建失败继续使用旧快照，并记录结构化错误。
- 同一时刻只允许一个刷新任务构建快照。
- FIFA 或历史同步流程：
  1. 导入数据库事务。
  2. 事务提交后构建新快照。
  3. 构建成功后原子替换。
  4. 构建失败时数据库保留新数据，但服务继续使用旧快照，并标记 `runtime_stale=true`。
- API 状态接口展示数据库指纹、快照指纹和是否滞后。
- `/api/status` 同时返回快照构建时间、训练截止时间、各模型状态和最近一次刷新错误摘要，不返回内部堆栈。

推荐提交顺序：

```text
refactor: 抽取共享预测服务
refactor: 统一特征构建和模型产物校验
fix: 原子刷新全部历史依赖模型
```

## 6. PR 4：可信 Walk-forward 与准入报告

### 6.1 数据门禁与时间切分

数据进入正式准入前必须满足：

- 至少 1500 场标准、完场、可训练比赛。
- 最终保留集至少 300 场。
- 单赛事结论要求该赛事在保留集至少 100 场。
- 未匹配球队、非法比分、未来赛果和来源冲突记录全部排除。

按时间分割：

- 最早 60%：初始训练。
- 中间 20%：验证与概率校准。
- 最后 20%：冻结保留集。
- 同一自然日不得跨越两个集合。
- 验证集用于模型选择，不计入最终准入样本。
- 准入结论只依据冻结保留集。
- 数据不足仍可生成报告，但状态只能是 `insufficient_data` 或 `research_only`。
- 当前历史数据规模预计低于正式准入门槛，因此 PR 4 的完成标准是生成可信、可复现的研究报告，不预设会有模型获得 `admitted`。

### 6.2 Walk-forward 流程

对每个时间批次：

1. 查询该批次开始前的训练数据。
2. 构建对应 `ModelRuntimeSnapshot`。
3. 对批次内所有比赛调用共享预测服务。
4. 保存模型概率、实际结果、警告、数据指纹和快照 ID。
5. 批次全部预测完成后，才将赛果加入历史。
6. 日期级记录以整日为批次；分钟级记录以相同开赛时间为批次。
7. 固定随机种子 42。
8. 蒙特卡洛只生成派生分布，不作为候选模型计分。

### 6.3 基线与候选模型

基线：

- 扩展窗口赛事胜平负比例。
- 最近 100 场赛事胜平负比例。
- 泊松。
- ELO。
- 真实、开赛前且去水后的市场赔率。

候选模型：

- Dixon-Coles
- Massey
- Form
- Head-to-Head
- Bayesian
- KNN

XGBoost、神经网络和 Stacking 在没有合规时间点产物时标记 `not_evaluated`。

### 6.4 指标与不确定性

指标使用固定定义：

- Multiclass Brier Score：`sum((p_k - y_k)^2)`，不除以类别数，范围 `[0, 2]`。
- Log Loss：`-log(clip(p_actual, 1e-15, 1))`。
- Ranked Probability Score：类别顺序固定为主胜、平局、客胜，计算前两个累计类别误差平方和并除以 `K-1`。
- 10 桶 top-label ECE：按最大预测概率分入 `[0, 0.1)` 至 `[0.9, 1.0]`，按桶样本占比加权准确率与平均置信度的绝对差。
- 命中率，仅辅助展示
- 按赛事、赛季、中立场、球队类型和数据覆盖等级分组指标

样本对齐规则：

- 模型与基线比较只使用双方均产生合法概率的样本交集。
- 每个模型同时报告 `eligible_samples`、`valid_predictions` 和覆盖率。
- 市场赔率仅使用 `captured_at` 不晚于预测时点且严格早于开赛时间的快照。
- 不同模型不得使用各自不一致的样本集合后直接比较汇总指标。

不确定性：

- 按赛事和连续 7 天组成重采样块，在同一配对样本上重采样。
- Bootstrap 2000 次，随机种子 42。
- 输出均值、95% 区间和相对基线差值。

### 6.5 准入规则

总体准入必须同时满足：

- 保留集有效样本不少于 300。
- 平均 Log Loss 至少比扩展窗口赛事比例基线低 `0.005`。
- Log Loss 配对 bootstrap 差值的 95% 上界不大于 0。
- RPS 和 Brier 均不差于基线。
- ECE 不得比基线高 `0.02` 以上。
- 有效预测覆盖率不得低于符合数据门禁样本的 95%。
- 特征、产物和训练时间验证全部通过。

单赛事准入额外要求：

- 该赛事保留集不少于 100 场。
- 该赛事单独满足相同指标门槛。

状态只能为：

- `admitted`
- `research_only`
- `insufficient_data`
- `not_evaluated`
- `rejected`

本阶段只生成准入清单，不自动修改线上权重。线上权重变更必须另开 PR 审查。

### 6.6 CLI 契约

重写 `calibrate_cli.py`：

```powershell
python calibrate_cli.py backtest --database PATH
python calibrate_cli.py backtest --fixture PATH
python calibrate_cli.py backtest --fixture PATH --allow-insufficient-data
python calibrate_cli.py report --run-id ID
python calibrate_cli.py admission --run-id ID
```

执行规则：

- 回测命令始终离线，只读取显式数据库或仓库内 fixture；联网同步只能通过独立的受保护数据同步入口执行。
- `calibrate.py` 保留为兼容入口，只打印弃用说明并转发新 CLI。
- 成功返回 0。
- 配置或数据非法返回 1。
- 数据不足、仅能生成研究报告时返回 2。
- `--fixture` 只能使用仓库内固定测试数据，不写线上权重。
- `--allow-insufficient-data` 只允许与 `--fixture` 同时使用：成功生成 `insufficient_data` 研究报告时返回 0；与 `--database` 同时使用属于配置错误并返回 1。

输出到被忽略的目录：

```text
data/processed/backtests/<run_id>/
  manifest.json
  status.json
  predictions.jsonl
  metrics.json
  report.md
  admission.json
```

`manifest.json` 必须记录 Git 提交、数据库指纹、数据范围、特征版本、参数版本、随机种子和运行时间。`status.json` 记录 PID、`queued|running|completed|failed|interrupted` 状态、开始与结束时间、退出码和错误摘要，并使用临时文件加 `os.replace()` 原子更新。

### 6.7 Web 校准任务迁移

- `POST /api/calibrate/run` 启动 `calibrate_cli.py backtest --database <configured-path>`，不再直接运行旧 `calibrate.py`。
- 接口在启动前生成 `run_id`，成功返回 `{"status":"started","run_id":"..."}`。
- `GET /api/calibrate/status?run_id=<id>` 和 `GET /api/calibrate/report?run_id=<id>` 查询指定运行；兼容无参数调用时读取最近一次运行。
- 同一时刻只允许一个回测任务运行；重复启动返回 409 和当前 `run_id`。
- 服务启动时扫描未结束的 `status.json`：进程仍存在则恢复查询，进程不存在则原子标记为 `interrupted`。
- 子进程参数使用列表传递，不经 shell；API 只返回稳定错误码和中文摘要，内部堆栈写服务端日志。

推荐提交顺序：

```text
feat: 建立严格时间批次回测
feat: 增加回测指标和不确定区间
feat: 生成研究级模型准入报告
refactor: 统一校准命令行入口
```

## 7. 测试与验收

### 7.1 自动化测试

必须新增：

- 比赛幂等导入、改期、比分修正和疑似重复测试。
- 球队别名来源隔离、类型隔离和未匹配重处理测试。
- JSON dry-run 不创建数据库测试。
- SQLite 正式迁移、重复迁移和事务回滚测试。
- OpenLigaDB、500.com 和 FIFA 适配器解析、空结果与失败状态测试。
- 相同来源载荷跳过、来源新修订更新和导入统计测试。
- 日期级和分钟级 Walk-forward 无未来数据测试。
- 未来模型产物拒绝加载测试。
- Web、CLI、调试和回测同快照结果一致测试。
- 原子刷新失败后旧快照继续服务测试。
- 管理令牌、POST 方法和非回环启动测试。
- 相同数据、参数和种子重复回测结果一致测试。
- 数据不足不生成 `admitted` 状态测试。
- 模型覆盖率不足 95% 不生成 `admitted` 状态测试。
- Web 校准 run ID、任务互斥、状态恢复和中断识别测试。

### 7.2 最终门禁

```powershell
python -m compileall -q .
pytest -q
python scripts/migrate_history.py --source tests/fixtures/legacy_history.json
python calibrate_cli.py backtest --fixture tests/fixtures/matches.json --allow-insufficient-data
```

### 7.3 浏览器验收

- 无赔率时市场模型显示不可用。
- 蒙特卡洛显示为派生模拟且权重为 0。
- 有效模型数量不包含蒙特卡洛。
- 管理操作正确处理未配置、未授权、成功和失败状态。
- FIFA 同步后快照指纹与数据库指纹一致。
- 页面无控制台错误，预测、历史和彩票只读功能正常。

## 8. 文档与完成定义

每个 PR 同步 README 和对应设计文档。最终使用 `neat-freak` 核对代码、README、`docs/`、`AGENTS.md` 和本地 Agent 记忆。

本阶段完成必须同时满足：

- 四个 PR 均可独立审查和回滚。
- 默认测试完全离线。
- 应用启动不隐式抓取、训练或修改历史数据。
- 日期级数据不会被伪装成精确时间。
- 线上与回测共用预测和特征路径。
- 报告能追踪到代码、数据、参数和模型产物。
- 数据不足时不会输出虚假模型准入结论。
- 不提交数据库、缓存、报告、权重、模型产物或个人工具状态。
