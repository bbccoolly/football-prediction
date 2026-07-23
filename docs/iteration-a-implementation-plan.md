# 迭代 A：预测正确性基座实施方案

> 实施状态：已于 2026-07-23 在 `codex/prediction-correctness` 分支完成，等待代码审查与合并。

## 1. 实施目标

本轮只实施优化路线图的阶段 0 和阶段 1：

- 建立离线测试基座。
- 保证 ELO 初始化幂等且并发安全。
- 建立统一模型结果协议。
- 迁移旧权重并过滤不可用模型。
- 统一普通预测、调试预测和 CLI 的模型编排。
- 将“置信度”修正为“模型一致度”。

本轮不训练神经网络、不重写回测、不引入 SQLite、不处理球队别名。实施分支使用 `codex/prediction-correctness`。

## 2. 详细实施步骤

### 步骤 1：建立修复前基线

- 新增 `scripts/baseline_report.py`，只读取历史库、权重和模型文件，不导入 Flask 应用、不联网、不自动训练。
- 报告分别输出到被 Git 忽略的：
  - `data/processed/baseline-before.json`
  - `data/processed/baseline-after.json`
- 报告记录：
  - Git 提交号和生成时间。
  - 历史比赛数量及赛事分布。
  - ELO 文件格式、球队数量和历史条数。
  - 权重键、非法键和未匹配键。
  - XGBoost、神经网络、Stacking 的训练状态。
  - 当前数据指纹。
- 在修改行为前先生成 `baseline-before.json`。

### 步骤 2：建立离线测试基础设施

- 新增 `requirements-dev.txt`：
  - `pytest>=8,<9`
  - `pytest-cov>=5,<7`
  - `pytest-socket>=0.7,<1`
- 新增 `pytest.ini`：
  - `testpaths=tests`
  - 默认 `--disable-socket`
- 建立 `tests/unit/`、`tests/integration/`、`tests/fixtures/`。
- 固定比赛样本包含：
  - 日期乱序。
  - 国家队和俱乐部。
  - 中立场和非中立场。
  - 完全重复记录。
  - 缺少日期和比赛 ID 的记录。
  - 未知球队。
- 测试全部使用临时目录，不读取或修改真实 `data/processed/`。

### 步骤 3：实现 ELO 确定性重建

修改 `EloRating`：

- 构造函数增加可选 `storage_path`，默认仍使用当前路径。
- 新增 `reset()`，清空评分、历史和元数据。
- 新增规范化方法，排序字段依次为：
  - `date_time` 或 `date`
  - `match_id`
  - `league`
  - 主队、客队
  - 主客队比分
  - 中立场、重要性
- 日期统一转换为 UTC；无时区日期按 UTC 处理；无法解析时使用原始字符串作为稳定后备键。
- 不在本阶段自动删除重复记录。完全重复的记录仍计入指纹和重建，因为没有稳定比赛 ID 时无法证明它一定是重复数据。
- 使用排序后的完整规范记录计算 SHA-256 数据指纹。
- 新增 `rebuild(matches)`：
  - 从固定初始评分开始。
  - 按规范顺序处理全部记录。
  - 生成 `schema_version=2` 元数据。
- v2 文件保存：
  - `schema_version`
  - `ratings`
  - `history`
  - `data_fingerprint`
  - `parameter_fingerprint`
  - `match_count`
  - `built_at`
- 参数指纹包含 ELO 初始值、K 值、主场加成和尺度参数。
- `load(expected_fingerprint=None) -> bool`：
  - 文件不存在、JSON 损坏、schema 不兼容、数据指纹或参数指纹不同均返回 `False`。
  - 不在 `load()` 内隐式重建。
- `save()` 使用同目录临时文件和 `os.replace()` 原子写入。

初始化流程调整为：

1. 加载历史比赛。
2. 计算数据指纹。
3. 尝试加载匹配的 v2 ELO。
4. 加载失败且历史非空时完整重建并保存。
5. 历史为空时不覆盖已有文件，使用默认评分并记录警告。
6. 禁止“先加载，再对完整历史执行 `batch_update()`”。

`batch_update()` 保留为显式增量 API，供回测临时实例使用，但 Web 初始化不得调用它。

### 步骤 4：增加模型初始化并发保护

- 在 Web 模块增加 `_model_init_lock = threading.RLock()`。
- `_init_models()` 在锁内进行二次 `_initialized` 判断，避免多个请求同时初始化和训练。
- FIFA 同步不能直接修改正在提供预测的模型：
  - 先在局部变量中构建新的 ELO 和泊松模型状态。
  - 构建成功后在锁内一次性交换全局模型引用。
  - 构建失败时保留旧模型。
- 预测请求只读取已完成初始化的模型，不观察半更新状态。

### 步骤 5：建立统一模型结果协议

新增 `ensemble/prediction_contract.py`，提供：

- `normalize_prediction(model_id, raw_result)`
- `validate_probabilities(result)`
- `NoAvailableModelsError`

规范字段：

```json
{
  "model_id": "poisson",
  "model_version": "1",
  "available": true,
  "status": "ready",
  "home_win": 0.42,
  "draw": 0.29,
  "away_win": 0.29,
  "data_quality": null,
  "warnings": []
}
```

处理规则：

- 规范模型 ID 由预测字典键决定；原 `model` 字段保留。
- `status=not_trained/error`、`data_valid=false` 时不可用。
- KNN `neighbors_found=0` 时不可用。
- 概率包含 NaN、无穷值、负数或总和为 0 时不可用。
- 概率和与 1 的误差不超过 `0.02` 时归一化并增加警告。
- 概率和误差超过 `0.02` 时不可用，避免掩盖模型计算错误。
- `data_quality` 缺失时为 `null`，不能默认伪装为 `1.0`。
- 模型原有比分、预期进球、赔率和解释字段全部保留。
- 神经网络和 XGBoost 加载时校验特征维度；不兼容产物标记为不可用。

### 步骤 6：统一模型编排入口

在 Web 层抽取内部 `_run_predictions(context, report_progress=None)`：

- 普通 `/predict` 与 `/api/debug_predict` 共用同一模型调用、协议适配、蒙特卡洛和融合流程。
- 调试接口只在共用结果之外补充公式和中间数据，不重复实现模型预测。
- CLI 同样使用协议适配和 BMA 过滤逻辑。
- 蒙特卡洛只接收可用的基础模型。
- 蒙特卡洛没有可用输入时返回 `available=false`，不使用固定先验。
- 进度总数继续为 12；不可用模型显示“不可用”，但视为已执行完成。

### 步骤 7：迁移权重并修复融合

修改 BMA：

- 权重文件 schema 升级为 v2。
- 加载时执行 `knn -> knn_similar`。
- 若两个键同时存在，以 `knn_similar` 为准并记录警告。
- JSON 损坏时记录错误并回退 `INITIAL_WEIGHTS`，不覆盖损坏文件。
- 未知键、负数、NaN 和无穷权重全部丢弃。
- 缺失规范键从 `INITIAL_WEIGHTS` 补齐。
- 本次融合只保留 `available=true` 的模型并重新归一化。
- 所有模型均不可用时抛出 `NoAvailableModelsError`。
- 预期进球和比分按实际权重聚合，不再按模型数量平均。
- 返回：
  - `configured_weights`
  - `effective_weights`
  - `excluded_models`
  - 兼容字段 `weights=effective_weights`

### 步骤 8：调整 API 响应

`/predict` 新增：

- `model_agreement`
- `model_summary`
- `warnings`

`model_summary` 包含：

- `total_models`
- `available_models`
- `excluded_models`
- `unknown_quality_models`
- `using_defaults_models`

兼容策略：

- 本轮保留 `confidence`，值与 `model_agreement` 相同。
- 下一主版本再删除 `confidence`。
- 本轮保留 `ensemble.weights`，值等于 `effective_weights`。

统一错误响应保持当前 `error` 字符串兼容：

```json
{
  "error": "当前没有可用预测模型",
  "error_code": "NO_AVAILABLE_MODELS",
  "details": []
}
```

状态码：

- 请求体不是 JSON：400 `INVALID_JSON`
- 球队缺失：400 `MISSING_TEAMS`
- 球队相同：400 `SAME_TEAM`
- 赔率只填写部分、非有限值或小于等于 1：400 `INVALID_ODDS`
- 所有模型不可用：503 `NO_AVAILABLE_MODELS`
- 未预期内部错误：500 `INTERNAL_ERROR`

### 步骤 9：调整前端和 CLI

- 页面将“置信度”改为“模型一致度”。
- 优先读取 `model_agreement`，缺失时读取 `confidence`。
- 模型表使用 `effective_weights`。
- 不可用模型显示状态和原因，不显示固定概率。
- 显示“有效模型数 / 总模型数”。
- CLI 对不可用模型输出“不可用”，不将其固定概率打印为正常结果。

### 步骤 10：生成修复后报告并更新文档

- 执行完整测试和编译检查。
- 生成 `baseline-after.json`。
- 对比 before/after：
  - ELO schema 和指纹。
  - 权重键迁移。
  - 模型可用状态。
  - 有效模型数量。
- 更新 README/API 文档，说明：
  - `model_agreement` 含义。
  - `confidence` 已废弃但暂时兼容。
  - 未训练模型不会进入融合。
  - ELO v1 文件会自动重建。

## 3. 测试与验收

必须覆盖：

- 相同历史连续初始化三次，ELO 评分和指纹一致。
- 乱序输入得到相同结果。
- 增加一条完全重复记录会改变指纹，防止本阶段错误去重。
- v1、损坏 JSON、参数变化和指纹变化都会触发安全重建。
- 空历史不会覆盖现有产物。
- 两个线程同时初始化只执行一次重建。
- FIFA 同步构建失败时旧模型继续可用。
- `knn` 正确迁移为 `knn_similar`。
- 损坏权重文件回退初始权重但不被覆盖。
- 未训练神经网络、空 KNN 和维度不兼容 XGBoost 权重为 0。
- 概率和误差小于等于 0.02 时归一化；超过时排除。
- 单个模型 NaN 不影响其他有效模型。
- 全部模型无效时 API 返回 503。
- 普通预测和调试预测的模型结果一致。
- 测试期间所有网络请求被阻止。
- 测试不修改真实数据与模型文件。

执行命令：

```bash
python -m compileall -q .
pytest -q
git diff --check
```

## 4. Git 交付顺序

按以下中文提交拆分：

```text
test: 建立预测核心回归测试基座
chore: 增加只读模型基线诊断
fix: 重建 ELO 并保证初始化幂等
fix: 增加模型初始化并发保护
refactor: 统一模型预测结果协议和编排
fix: 迁移模型权重并过滤不可用模型
feat: 展示模型一致度和有效模型状态
docs: 更新预测接口与模型状态说明
```

每次提交只包含对应代码、测试和必要说明，不提交 before/after 运行报告、缓存或模型产物。

## 5. 假设与边界

- 本轮按推荐范围实施迭代 A。
- 本轮允许新增内部预测编排函数，但不进行完整 Flask Blueprint 重构。
- 完全重复比赛的业务去重延后到阶段 2，避免在没有稳定比赛 ID 时误删合法记录。
- `confidence` 至少保留一个主版本作为兼容字段。
- 足球、竞彩和彩票模块继续保持独立。

## 6. 实施结果

- 已建立离线 pytest 测试体系，默认阻断网络访问。
- ELO 持久化已升级至 schema v2，相同数据重复启动不再改写评分文件。
- 普通预测和调试预测已共用模型编排入口。
- 旧 `knn` 权重会迁移为 `knn_similar`，不可用模型的实际权重为 0。
- 神经网络无训练产物时明确显示不可用，不再以固定概率参与融合。
- 页面和 CLI 已改用“模型一致度”，并显示实际有效模型。
- `confidence` 和 `ensemble.weights` 仍作为兼容字段保留。
