# Review：Phase 10 Metadata Filter（含 Phase 9 Review 收口）

- 分支：`phase-10-metadata-filter`
- 评审时间：2026-10-20 16:30（Asia/Shanghai）
- 评审范围：上次 review（`review/review-phase-9-hybrid-20261020-0532.md`，基线
  `2c179e0`）之后的新增提交
  - `0ad304c` fix(hybrid)：收口 Phase 9 Review 的契约与排序问题
  - `5280fef` feat(filters)：实现 Phase 10 元数据过滤
  - `22d130e` fix(experiment)：避免未跟踪文件污染实验脏标记
  - `3182a81` docs(filters)：记录 Phase 10 干净实验结果
- 变更规模：`git diff --stat 2c179e0..3182a81` -> 33 files changed, 3920
  insertions(+), 76 deletions(-)
- 当前 HEAD：`3182a8111b5fc8802b24b307652862cadd680fa4`

## 验证结果

以下为本次实际执行并观察到的结果：

- SQLite 默认路径：`519 passed, 17 skipped`
- `uv run ruff check .`：`All checks passed!`
- Phase 10 dry-run：通过；数据集 SHA-256 前缀 `34bccafb4551`、22 条 Query、
  47 个 Chunk、`candidate_k=20`、`top_k=5`、`rrf_k=60`、`mode=postgresql`
- Phase 9 dry-run：通过；数据集 SHA-256 前缀 `be85ce63b760`、24 条 Query、
  47 个 Chunk、`candidate_k=20`、`top_k=10`、`fusion=all`、RRF 1:1、
  `rrf_k=60`
- Phase 8 dry-run：通过；输出 `top_k=10`、`k_values=[5, 10]`
- `scripts/hybrid_sweep.py --help`：通过
- `git diff --check`：通过
- 手工探针确认：SQLite 下 `metadata={"priority": 1}` 会同时匹配过滤值
  `"1"` 与数字 `1`；JSON 数组中的数字元素也会进入数组 contains-any 匹配。

`uv run alembic check` 未完成：本机 PostgreSQL 17 在 `127.0.0.1:5432` 拒绝
连接，Alembic 尝试连接时返回 `Connection refused`。本次也没有执行
PostgreSQL 全量测试或重建真实 pgvector 实验。因此 PG 专属谓词、真实延迟与
manifest 数值的复核边界是“代码与 SQLite 已验证、离线数据一致；PG 路径未在本
轮实机复验”。已发布 manifest 显示其来自干净提交 `22d130e` 且
`mode=postgresql`，但没有证据表明本次评审时 PostgreSQL 仍可用。

---

## 1. 总体结论

Phase 10 的阶段边界整体守住了：没有提前改统一 `/search` 或 RAG 的公开契约，
过滤器集中在 Chunk 级服务接缝；受控字段、fail-fast 校验、数组 contains-any、
多字段 AND、缺失值 no-match 与 `LIMIT` 前过滤都有跨方言测试；实验独立 fixture
没有改写 Phase 8/9 历史数据集；`during` 明确声明当前只是 `pre` 的别名，未虚构
HNSW / ANN 结论。Phase 9 review 提出的主要契约、排序、可追溯性与报告一致性
问题也已实质收口。

但当前实现存在一个高优先级类型契约缺陷：**标量过滤没有在 SQL 层锁定 JSON
类型，SQLite 会把数字 `1` 与字符串 `"1"` 视为相等**。这与“错误类型不匹配”的
文档约定不一致；当前数据集恰好都是字符串，所以已发布 Phase 10 指标不受影响，
但生产数据一旦出现数字或 JSON 数组中的数字，过滤器会静默扩大召回。

报告与 manifest 还有延迟口径不一致的中优先级问题；实验用
`--untracked-files=no` 让未跟踪源码 / 数据不影响 dirty 标记，也留下可追溯性
缺口。小样本负例的 FPR 为 1.0，但报告没有把“Metadata Filter 不解决拒答 /
误召回”表达为明确限制。

总体上，**Phase 10 可作为 Chunk 级实验接缝进入 Phase 11；在生产或更大库使用
前，应先修正 SQL 类型契约与延迟 / 溯源口径。**

---

## 2. 阻断性问题

无。

当前 Phase 10 fixture 的标量元数据均为字符串，且过滤条件经过
`normalize_metadata_filters()` 强制为字符串；已发布实验指标和当前默认数据路径
没有因此产生错误结果。高优先级问题限制的是生产元数据的类型兼容性，不否定本
轮实验结论。

---

## 3. 高优先级发现

### 3.1 SQLite 标量过滤缺少 JSON 类型约束，数字与字符串静默互配

- 位置：`app/retrieval/filters.py:107-109`、
  `app/retrieval/filters.py:175-178`
- 文档约定“缺失字段、`null`、类型错误都不匹配”
  （`docs/METADATA_FILTER.md:49-50`），但标量谓词只使用
  `meta[field].as_string() == value`。
- 本次 SQLite 探针确认：
  - `metadata={"priority": 1}` 能匹配 `{"priority": "1"}`；
  - JSON 数组中的数字元素也能匹配字符串 contains-any 条件；
  - 反向输入如 `{"priority": 1}` 会被 `normalize_metadata_filters()` 拒绝，
    因此调用侧无法用相同类型条件消除该误配。
- SQLite 的 `json_extract(...) = '1'` 与 Python `matches_metadata()` 的
  `actual != expected`（`app/evaluation/metadata_filter_experiment.py:66-78`）
  在类型语义上不一致。
- PostgreSQL 路径未实机复验；`->>` 转文本同样可能把数字 `1` 与字符串 `"1"`
  视为相等。已发布的 Phase 10 fixture 只使用字符串值，因此 manifest 指标不
  受此问题影响。

**影响**

知识库来自 Schema 与人工输入，`version`、`priority` 等长期使用字符串，所以
当前实验与测试都未触发。但 Metadata 列本身是自由 JSON，用户可通过通用导入或
后续契约变化写入数字、布尔或混合数组。此时过滤会静默返回错误类型的候选，且
跨方言结果可能不同。这违背过滤器“缺失 / 错误类型不匹配”的显式语义，属于生
产可用性风险。

**建议**

1. 在 SQL 层同时校验 `json_type` / JSONB 类型，例如标量要求 string，
   数组要求 array 且元素类型为 string；
2. 为 SQLite 和 PostgreSQL 增加“数字 vs 字符串”“布尔 vs 字符串”“数组混合
   类型”的跨方言回归测试；
3. 若决定把过滤器定位为“文本化匹配”，必须在代码契约、文档与
   `filter_semantics` 中显式改写，不能继续声称类型错误不匹配。

---

## 4. 中优先级发现

### 4.1 报告的延迟口径与计时实现不一致

- 位置：`app/evaluation/metadata_filter_experiment.py:243-322`、
  `docs/METADATA_FILTER.md:112-119`、
  `results/metadata-filter-phase10.json:255`
- 实验在每次 Query 循环中先调用 `time.perf_counter()`，再执行 `prefilter()`，
  最后在排序 / 融合后停止计时。真实运行时 `prefilter()` 通过
  `scripts/metadata_filter_experiment.py:380-408` 发起 SQL `WHERE` 并取回
  结果。
- 但报告第 6 节说延迟“只包含候选排序与融合”，manifest 的
  `latency_scope` 也写成 `"candidate ranking and fusion only"`。
- 因此 `pre` / `during` 的 P50 / P95 实际包含 SQL 过滤与候选取回，而报告
  描述排除了这部分；跨变体延迟对比也不是同一工作负载。

**影响**

读者可能把 `pre` 低于 `none` 的延迟误解为过滤本身“免费”或数据库路径更快，
从而影响生产路径判断。指标没有损坏，但实验口径表达失真。

**建议**

- 将文档和 `latency_scope` 改为“prefilter（如启用）+ 取回候选 + 排序 / 融合，
  不含 Query Embedding 与建索引”；
- 或分段计时，把 SQL prefilter、候选取回与排序 / 融合分别写入 manifest；
- 报告测试应覆盖 `latency_scope` 与实际计时边界的一致性。

### 4.2 未跟踪实现或数据不会让实验 dirty 标记生效

- 位置：`scripts/metadata_filter_experiment.py:90-106`、
  `tests/test_metadata_filter_experiment.py:59-86`
- `_worktree_dirty()` 使用
  `git status --porcelain --untracked-files=no`，动机是避免长期存在的评审
  文件和输出产物污染标记；这个取舍本身合理。
- 但它同样忽略了未跟踪的源码、fixture、模型配置或实验脚本。如果这些文件被
  当前进程使用，manifest 仍会记录 `git_dirty=false`。
- 已发布 manifest 使用了该逻辑：`git_commit=22d130e`、`git_dirty=false`。没有
  证据表明当时存在影响输入的未跟踪文件，但审计链无法排除这一类来源。

**影响**

正式实验的可复现性从“提交 + 输入哈希”退化为“提交 + 数据集哈希 + 候选哈希”。
若未跟踪的代码或环境文件参与生成，manifest 会给出过强的干净承诺。

**建议**

1. 将 `--untracked-files=no` 收窄到明确的非关键路径（例如 `review/`、
   `results/`、`/tmp` 产物），对 `app/`、`scripts/`、`eval/`、`docs/` 仍检查
   未跟踪文件；
2. 或在 manifest 增加 `untracked_paths_ignored` 与关键输入路径清单，使取舍
   显式可审计；
3. 至少在报告中说明 dirty 标记只覆盖 tracked changes，不覆盖未跟踪输入。

### 4.3 负例误召回结果未在报告中给出明确限制

- 位置：`results/metadata-filter-phase10.json:359-393`、
  `results/metadata-filter-phase10.json:442-476`、
  `results/metadata-filter-phase10.json:525-559`、
  `results/metadata-filter-phase10.json:608-642`、
  `docs/METADATA_FILTER.md:95-110`
- manifest 显示 4 条不可回答 Query 在 `none` / `pre` / `during` 的 FPR 均为
  `1.0`，`post` 为 `0.75`；负例 top1 distance 多数为 0。
- 报告只讨论 Recall、MRR、空结果、Gold 被过滤和候选饥饿，没有把 FPR 和
  “Metadata Filter 不解决拒答 / 误召回”列入结果或限制。

**影响**

小样本 FPR 不能外推，但当前数据已经明确提示：即使过滤有效，下游仍需要单独
的拒答 / 相关性判定。省略该限制容易让 Phase 10 结论被读成检索质量整体改善。

**建议**

- 在结果表或限制章节明确记录 4 条不可回答 Query 与各变体 FPR；
- 说明 Metadata Filter 只约束候选空间，不负责“无证据拒答”；
- 后续 Phase 11 Rerank 或 RAG 评估中单独跟踪负例指标。

---

## 5. 低优先级发现

### 5.1 `candidate_k` 对单通道与后过滤路径的作用需要更明确

- 位置：`app/evaluation/metadata_filter_experiment.py:168-201`、
  `scripts/metadata_filter_experiment.py:181-247`、
  `docs/METADATA_FILTER.md:93-97`
- `_rank()` 只在 RRF 中把 `candidate_k` 作为两个通道深度；`vector` 与
  `keyword` 单通道使用 `top_k`。`post` 也是先取 `top_k` 再过滤。
- manifest 和报告只写 `candidate_k=20`，没有区分它在 RRF 通道深度、单通道
  深度与 post 路径中的不同作用。

**影响**

默认 RRF 实验自洽；但复用 `--strategy vector|keyword` 或解读 `post` 时，可能
误以为会先取 20 个候选再过滤。

**建议**

- 在 manifest 增加 `candidate_k_semantics` 或按 strategy 写入实际通道深度；
- 报告明确 `candidate_k` 当前只影响 RRF 的两路候选深度。

### 5.2 `post` 的 under-K 数量被空结果数字掩盖

- 位置：`results/metadata-filter-phase10.json`（各 `result_counts`）、
  `docs/METADATA_FILTER.md:95-110`、
  `tests/test_metadata_filter_report.py:40-51`
- manifest 中 `post` 有 1 条空结果，且 21/22 个 Query 返回少于 `top_k=5`；
  `pre` / `during` 也有 15/22 个少于 5，这是过滤选择性的自然结果。
- 报告只突出 1 条空结果，没有给出 under-K 数量或结果数分布，读者较难判断候
  选饥饿的表现范围。

**影响**

不是指标错误；但候选饥饿的可见性依赖读者自行解析原始 `result_counts`。

**建议**

- 报告表增加 under-K Query 数；
- manifest 可汇总 `below_top_k_queries`；
- 测试同步校验该汇总，避免报告只挑最有戏剧性的空结果。

---

## 6. Phase 9 Review 收口核对

| Phase 9 发现 | 当前状态 | 依据 |
|---|---|---|
| `--fusion` 不控制实际通道 | 已收口 | `fusion` 支持 `all / vector / keyword / weighted / rrf` 并构造 variants；manifest 记录 `selection` 与 `evaluated_variants`；脚本校验二者一致（`app/evaluation/hybrid_experiment.py:198-239`、`scripts/hybrid_experiment.py:216-226`、`results/hybrid-phase9.json:57-65`） |
| `higher_is_better` 被忽略 | 已收口 | Weighted 按通道方向归一化；混合方向 fail fast（`app/retrieval/hybrid.py:85-99`、`app/retrieval/hybrid.py:135-162`） |
| 向量生产查询缺稳定 tie-break | 已收口 | 服务层按 distance 加来源身份排序；PG 测试覆盖相同向量平局（`app/services/knowledge_chunk_service.py:190-196`、`tests/test_retrieval_pg.py:84-134`） |
| 通道候选深度配置未分别生效 | 已收口 | 提供 `vector_candidate_k` / `keyword_candidate_k`，显式值优先并分别传给通道（`app/services/knowledge_chunk_service.py:260-343`） |
| 检索配置缺少正数与 final-depth 校验 | 已收口 | 四类检索值均为正数；final Top-K 不得大于两通道深度（`app/core/config.py:107-121`） |
| RRF 权重扫描报告只查行数 | 已收口 | 报告 RRF k 行按 manifest 的 Recall@5 / MRR 逐行比较（`tests/test_hybrid_report.py:185-199`） |
| 动态 Recall@K 硬编码 | 已收口 | `k_values_for()` 供 lexical / hybrid / sweep 使用（`app/evaluation/lexical_experiment.py:145`、`scripts/hybrid_sweep.py:260`） |
| 权重 / 分数必须有限 | 已收口 | 融合器拒绝非有限权重与分数（`app/retrieval/hybrid.py:97-99`、`app/retrieval/hybrid.py:118-125`） |
| 提交角色不清楚 | 已收口 | `manifest_schema_version=2` 增加 `result_path` 与 `git_commit_role=generation`（`results/hybrid-phase9.json:2-18`、`results/hybrid-sweep-phase9.json:2-18`） |
| Hybrid 缺端到端规模护栏 | 部分收口 | 剩余是容量观察项；当前仍明确标注 BM25 内存索引仅适用于小规模知识库（`app/services/knowledge_chunk_service.py:215-220`、`app/services/knowledge_chunk_service.py:284-287`） |

---

## 7. 纪律与设计 / 开发计划符合性

| 约束 / 计划项 | 结论 | 依据 |
|---|---|---|
| Phase 10 支持 `module` / `priority` / `version` / `status` / `type` | 符合并扩展 | 实现标量字段额外覆盖 `sub_module`、`severity`、`case_type`、`doc_type`，数组覆盖 `tags`、`related_cases`（`app/retrieval/filters.py:27-40`） |
| Filter before / during / post 比较与 Filter 对 Recall 的影响 | 符合边界 | 四个变体齐备；`during` 明确复用 `pre`，未声称 ANN（`app/evaluation/metadata_filter_experiment.py:225-322`、`docs/METADATA_FILTER.md:62-78`） |
| Approximate index / iterative scan | 明确未验证并如实记录 | manifest 记录 `ann_index=null` 与 `during` 边界（`scripts/metadata_filter_experiment.py:242-255`、`results/metadata-filter-phase10.json`） |
| 产出 `docs/METADATA_FILTER.md` | 符合 | 报告覆盖 DSL、变体、跨方言、结果、延迟、复现性与限制（`docs/METADATA_FILTER.md`） |
| 统一 `/search` 与 RAG 契约不提前变更 | 符合 | `app/api/search.py` 与 `app/services/search_service.py` 未接 Metadata Filter；报告和 handover 明确未接入 |
| 数据集纪律 | 符合 | 使用独立 `phase10_simulated.json`，测试证明过滤条件不会排除 Gold 证据来源（`tests/test_metadata_filters.py:125-161`） |
| 召回评估口径 | 基本符合 | Gold 以 source + evidence 锚定；小样本 / 非统计结论已声明（`app/evaluation/metadata_filter_experiment.py:155-165`、`docs/METADATA_FILTER.md:106-110`） |
| 干净实验与可追溯性 | 基本符合 | manifest 有 commit / dirty / dataset / chunks / result_path；但未跟踪输入被显式忽略（见 4.2） |
| PostgreSQL 纪律 | 本轮受限 | manifest 声明 PG 17 + pgvector；但评审时 PG 不可用，本轮未复验 |
| 不越过 Phase 11 Reranker | 符合 | 未实现 rerank 或公开 API 分数契约变更 |

---

## 8. 下一步建议

1. 优先补齐标量与数组元素的 JSON 类型谓词和跨方言类型回归测试；
2. 修正 `latency_scope` 与报告第 6 节，或引入分段延迟字段；
3. 将 dirty 检查的忽略范围改为显式非关键路径，并在 manifest / 报告说明
   untracked 边界；
4. 在报告中补充负例 FPR 与 under-K 分布，避免只呈现 Recall / MRR 改善；
5. 在 PostgreSQL 恢复后重跑 `alembic check` 与 PG 全量测试；
6. Phase 11 前保持统一 `/search` 与 RAG 契约不变，先由设计确认 Metadata
   Filter 的公开入口与参数来源。

