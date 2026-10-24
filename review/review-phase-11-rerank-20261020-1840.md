# Review：Phase 11 Hybrid + Rerank（进行中）

- 分支：`phase-11-rerank`
- 评审时间：2026-10-20 18:40（Asia/Shanghai）
- 评审范围：上次 review（`review/review-phase-10-metadata-filter-20261020-1630.md`，
  基线 `3182a81`）之后的新增提交
  - `98d4a15` fix(filters)：收口 Phase 10 review 的类型契约与报告口径
  - `df62284` feat(rerank)：实现 Phase 11 Hybrid + Rerank 接缝与实验
- 变更规模：`git diff --stat 3182a81..HEAD` -> 18 files changed, 1533
  insertions(+), 89 deletions(-)
- 当前 HEAD：`df62284384d2262210b8c99a4cb79ade83719900`

## 验证结果

以下为本次实际执行并观察到的结果：

- SQLite 默认路径：`533 passed, 17 skipped`
- `uv run ruff check .`：`All checks passed!`
- Phase 11 dry-run：通过；数据集 SHA-256 前缀 `9bb674c77319`、8 条 Query、
  47 个 Chunk、`candidate_k=20`、`rerank_candidates=20`、`top_k=5`、
  reranker 模型 `Qwen3.5-9B-DFlash`
- Phase 10 dry-run：通过；数据集 SHA-256 前缀 `34bccafb4551`、22 条 Query、
  47 个 Chunk
- Phase 9 dry-run：通过；数据集 SHA-256 前缀 `be85ce63b760`、24 条 Query、
  47 个 Chunk、`fusion=all`
- `git diff --check`：通过
- 手工探针：本地 OMLX 返回 409，`Qwen3.5-9B-DFlash` 因加载历史失败不可用；
  探针因此确认了客户端错误转换可用，但没有确认 `/rerank` 请求与响应契约
  在真实成功响应下可用。
- 手工探针：同一批候选与确定性反向 reranker 下，`Hybrid + Rerank` 的
  Recall@5 与 baseline 相同，但 MRR 从 0.167 变为 0.033。这说明当前实验
  确实只重排已经截断的最终 Top-K，而不是按计划重排 20 个融合候选。

`uv run alembic check` 未完成：本机 PostgreSQL 在 `127.0.0.1:5432` 拒绝连接。
本次也未执行 PostgreSQL 全量测试或真实 Hybrid + Rerank 实验。`docs/RERANK_EXPERIMENT.md`
与 `results/rerank-phase11.json` 尚不存在，本阶段还没有正式实验结果。

---

## 1. 总体结论

Phase 10 review 的类型契约、延迟口径、dirty 检查、FPR、under-K 与
candidate-k 语义问题都已实质收口；新增类型测试覆盖数字 vs 字符串、JSON
metacharacters、非数组容器与 untracked 关键路径，报告一致性测试也同步更新。

但 Phase 11 当前实现没有守住开发计划的 Pipeline：**`rerank_candidates=20`
只被当作最终截断长度，Reranker 实际收到的是 `top_k=5` 的 Hybrid 融合结果**。
这会把重排实验退化为“对 5 条结果重排”，无法评估在 20 条候选上把 Gold 从更
深处提升到 Top-5 的能力。此外，Reranker 协议要求返回数量等于
`min(top_n, len(hits))`，与 OpenAI-compatible rerank API 的 `index` +
`relevance_score` 成功契约不一致，会使真实可用服务被误判失败。

阶段纪律方面做得较好的是：Rerank 未提前接入 `/search` 或 RAG；实验库名有
防护；候选边界和身份校验明确；配置与 `.env.example` 已同步。不过，计划要求的
正式产物 `docs/RERANK_EXPERIMENT.md` 尚未生成，结果文件与 manifest 也缺失，
当前只能认为实现接缝处于进行中状态。

**不建议在修正 Rerank 深度与客户端成功契约前运行正式实验或将其结果用于
结论。**

---

## 2. 阻断性问题

### 2.1 Reranker 实际只重排最终 Top-K，`rerank_candidates` 没有控制候选深度

- 位置：`app/evaluation/hybrid_experiment.py:303-321`、
  `app/evaluation/hybrid_experiment.py:244-245`、
  `scripts/rerank_experiment.py:173-183`
- 开发计划要求 `Vector 20 + BM25 20 -> Fusion -> 20 candidates -> Rerank ->
  Top 5`（`docs/AI_Test_Knowledge_Platform_Implementation_Plan.md:479-487`）。
- `evaluate_hybrid()` 对 weighted / rrf 分支先执行：
  `fuse(..., limit=top_k)`，随后才调用 `rerank_fused_hits(..., top_n=top_k)`。
  因此 `rerank_candidates=20` 只成为校验下限和 vector 分支里的第二个融合
  截断值，不会进入 weighted / rrf 主路径。
- 本次确定性反向 reranker 探针显示 Recall@5 不变、MRR 改变，证实 reranker
  输入只有 5 个候选。若 Gold 在 Hybrid Top-5 之外但在 Top-20 内，reranker
  没有机会将其召回。
- `scripts/rerank_experiment.py` 的 dry-run 回显 `rerank_candidates=20`，
  manifest 也会记录相同值，进一步放大这种表面正确、实际未生效的风险。

**影响**

这不是统计噪声或口径措辞问题，而是实验变量未按设计执行：Recall@5 的上限
已经在 reranker 调用前被截断到 `top_k`。按当前实现得到的
`Hybrid + Rerank` Recall 永远不可能优于 Hybrid RRF 的 Recall@5，最多只能
通过改变 top-5 顺序改善 MRR。这直接违背 Phase 11 的核心研究问题。

**建议**

1. 在调用 reranker 前使用 `fuse(..., limit=rerank_candidates)` 生成 20 个
   融合候选；Hybrid baseline 的融合结果保持 `limit=top_k`；
2. `rerank_fused_hits(..., top_n=top_k)` 返回 Top-5；
3. 为 weighted / rrf 分支增加测试：baseline 与 rerank 通道的 reranker 输入
   数量必须等于 `rerank_candidates`，且 baseline 输入深度保持 `top_k`；
4. 增加 end-to-end 用例证明 Gold 在融合 Top-20 但不在 Top-5 时，可被
   reranker 提升；CLI / manifest 校验 reranker 实际输入数量。

### 2.2 Rerank 成功响应契约与 OpenAI-compatible API 不一致

- 位置：`app/rerank/service.py:92-151`、`app/rerank/hybrid.py:31-40`、
  `app/evaluation/hybrid_experiment.py:242-245`
- OpenAI-compatible `/v1/rerank` 的成功响应由带 `index` 与
  `relevance_score` 的 `results` 组成，`top_n` 表示服务器最多返回多少条；
  服务没有承诺所有候选都返回，也没有把响应数量绑定到 `min(top_n, N)`。
- `OpenAICompatibleRerankClient.rerank()` 却在
  `len(ranked) != min(top_n, len(candidates))` 时抛出
  `"Rerank 返回数量不一致"`。`rerank_fused_hits()` 再次执行同样的断言。
- 因此一个按 API 合法返回少于 `top_n` 的成功响应会被当作 503 上游失败；
  即使未来某些 provider 尊重 `top_n`，该约束也不是接口契约的一部分。
- 当前测试只覆盖 Pydantic 最小 payload、错误参数和身份错误，没有模拟
  “成功但返回少于 top_n”的 HTTP 响应。

**影响**

真实 provider 可能因候选评分阈值、内部截断或返回格式差异返回较少 results。
这会让真实实验在语义成功的响应上失败，浪费已产生的 embedding 与检索成本；
同时错误契约可能迫使上游实现做非标准行为，降低可替换性。

**建议**

1. 客户端契约改为：`results` 至少 1 条、index 合法、key 唯一、分数有限；
   不要求等于 `min(top_n, N)`；
2. `rerank_fused_hits()` 允许 `1 <= len(ranked) <= min(top_n, len(hits))`；
   若业务需要精确 Top-5，应在调用前确认 provider 支持该保证；
3. 增加 fake HTTP / fake client 测试覆盖成功但少于 `top_n` 的响应；
4. manifest 记录实际 rerank 返回数量，而不是只记录请求的 `top_k`。

---

## 3. 高优先级发现

---

### 3.1 Reranker 协议允许返回空结果，导致评测通道空结果静默合法

- 位置：`app/rerank/service.py:99-102`、`app/rerank/service.py:127-151`、
  `app/rerank/hybrid.py:26-40`
- `rerank()` 在空 candidates 时返回空 list；`rerank_fused_hits()` 在空 hits
  时也返回空 list，这是合理的早退。
- 但当 hits 非空而 provider 返回空 `results` 时，`rerank_fused_hits()` 的
  现有长度检查在修复 2.2 后会允许空结果；评测器会把该 Query 记为空结果。
- 对检索实验而言，Rerank 上游返回零条应被视为异常或至少显式记录，否则
  latency / FPR / Recall 会被混入“检索确实为空”的假象。

**影响**

在小样本 8 条 Query 中，一次异常空响应即可明显拉低 Recall 和 FPR，且不容易
从聚合指标发现根因。

**建议**

1. `rerank_fused_hits()` 对非空输入要求至少返回 1 条；
2. `evaluate_hybrid()` / manifest 分别记录 `rerank_empty_responses`；
3. 空响应测试与指标测试同步加入。

### 3.2 默认 Reranker 模型不可用，且未接入任何健康探测或预检

- 位置：`.env.example:34-39`、`app/core/config.py:49-55`、
  `app/rerank/service.py:74-90`
- 本地探针显示 `Qwen3.5-9B-DFlash` 当前返回 409，服务端记录其因此前加载
  失败不可用。dry-run 只打印模型名，不会发现模型不可用。
- `/health` 的 `capabilities.rerank` 仍基于 Key 配置，而非真实可用性；这与
  早期阶段对 embedding 的实际探测方向不一致。
- 正式 rerank 实验只有在完成 embedding、建表、chunk embedding 和 query
  embedding 之后才会调用 reranker；模型不可用会在大量工作后失败。

**影响**

不是代码可移植性问题，但会直接阻断 Phase 11 正式实验，并造成无谓的模型 / 
数据库准备成本。

**建议**

1. 在正式实验创建数据库表之前增加 reranker 预检，例如单候选 `/rerank`
   probe；
2. manifest 记录 reranker 预检状态、模型版本 / 服务元数据（如可用）；
3. 更换本地可用模型或在 `.env.example` 注释中说明默认模型的运行条件；
4. 若保留 capabilities 的配置型语义，在 handover 与实验文档中明确它不是
   可用性探测。

## 4. 中优先级发现

---

### 4.1 缺少 Phase 11 计划要求的正式产物

- 位置：`docs/AI_Test_Knowledge_Platform_Implementation_Plan.md:479-504`、
  `CHANGELOG.md:9-21`
- 计划要求比较 `Hybrid` 与 `Hybrid + Rerank`，并产出
  `docs/RERANK_EXPERIMENT.md`。当前 `docs/RERANK_EXPERIMENT.md` 和
  `results/rerank-phase11.json` 都不存在。
- CHANGELOG 标注“进行中”，所以这是阶段状态问题而不是虚假交付；但当前
  提交标题为“实现 Phase 11 Hybrid + Rerank 接缝与实验”，缺少正式产物容易
  被后续误读为实验完成。

**影响**

无法核对 Recall@5 / MRR / latency、负例表现、reranker 输入深度、模型版本与
干净运行条件；Phase 12 数据集建设也不应基于这种不可审计状态继续。

**建议**

1. 先完成阻断项修复，再从干净工作区生成 manifest；
2. 补充报告，包含候选深度、输入 / 返回数量、latency scope、负例 FPR 和
   8 条 Query 的小样本边界；
3. 更新 handover / README 阶段状态，明确“接缝已实现、正式实验未完成”。

### 4.2 单次 `evaluate_hybrid()` 无法同时返回 baseline 和 rerank 通道

- 位置：`app/evaluation/hybrid_experiment.py:194-248`、
  `app/evaluation/hybrid_experiment.py:252-321`、
  `scripts/rerank_experiment.py:238-252`
- 提供 `reranker` 后，当前 `fusion="rrf"` 只评测一个名为 `rrf` 的通道；
  baseline 与 rerank 的区分依赖调用方跑两次实验。
- 这会带来两个问题：两次运行的 vector SQL / BM25 理论上可能受环境波动影响；
  manifest 的 `results` 里两个 `name` / `strategy` 难以自然区分
  baseline 与 rerank。
- 脚本文档说“比较 Hybrid RRF 与 Hybrid + Rerank”，但当前实现没有构造这两
  个通道。

**影响**

不是当前测试失败的原因，因为还没有正式 manifest；但会削弱对照实验的可比
性和结果可读性。

**建议**

1. 在 Phase 11 模式下显式生成 `rrf` 与 `rrf_rerank` 两个通道，共享同一批
   chunk embedding、query embedding 与检索候选；
2. 或保留单通道模式，但 manifest 增加 `experiment_kind=baseline|rerank`、
   `paired_run_id`，报告固定要求同 run 对照；
3. 测试断言两个通道的 reranker 输入差异只来自候选深度，不来自数据重载。

### 4.3 reranker 调用前缺少最大候选 / 正文字数控制

- 位置：`app/rerank/service.py:92-120`、
  `app/evaluation/hybrid_experiment.py:303-321`
- 客户端只校验非空、重复身份和 finite 分数，没有限制单请求文档数量或总字符
  数。当前 47 行小语料不会触发问题，但 `rerank_candidates` 是 CLI 参数。
- 若用户设置较大的 `--rerank-candidates` 或候选正文很长，单请求可能超过本地
  模型上下文或 provider 限制，导致全实验失败。

**影响**

属于实验健壮性和成本边界问题，不影响当前小样本正确性。

**建议**

1. 配置或 CLI 增加 `rerank_max_batch_tokens` / `rerank_max_batch_documents`
   粗粒度上限；
2. 超限时 fail fast 或显式分批，避免隐式截断；
3. manifest 记录每请求候选数与正文长度摘要。

---

## 5. 低优先级发现

### 5.1 `HybridChannelResult` 未按通道区分 rerank 状态

- 位置：`app/evaluation/hybrid_experiment.py:47-69`、
  `app/evaluation/hybrid_experiment.py:258-342`、
  `scripts/rerank_experiment.py:149-160`
- `rerank_seconds` 目前只在 `HybridExperimentResult` 上记录总时长；
  `HybridChannelResult` 没有 `reranked: bool`、输入 / 返回数量或单通道耗时。
- 若同一 result 中混有普通 channel 与 rerank channel，读者需要根据 name 猜
  测其语义。

**建议**

1. 给通道结果增加 `reranked`、`rerank_input_candidates`、
   `rerank_returned_candidates`、`rerank_seconds`；
2. manifest 直接映射这些字段。

### 5.2 fake reranker 测试依赖反字典序，难以解释排序质量

- 位置：`tests/test_hybrid_rerank_experiment.py:15-29`、
  `tests/test_hybrid_rerank_experiment.py:46-65`
- 确定性 reranker 按 content 长度降序排序，测试只验证数量与身份。这能守护
  契约，但不能证明 reranker 输出按分数降序进入指标。
- OpenAI 响应里的 `results` 可能非降序，当前代码直接采用返回顺序。

**建议**

1. 增加乱序 `results` 用例，断言最终输出按返回顺序并进入指标；
2. 增加一个可预测提升 Gold 名次的 reranker 用例，而不仅是顺序还原。

### 5.3 OpenAI 客户端错误细节被完全吞掉

- 位置：`app/rerank/service.py:110-125`
- 上游异常统一转换为 `RerankError`，只保留 model，不保留异常类型或 status
  code。本地探针中 409 / model unavailable 的根因不能从抛出的领域错误直接
  看到。
- 隐藏凭据和用户内容是必要的，但状态码与错误类型对诊断是安全的。

**建议**

1. detail 增加 `error_type` 与 `status_code`（如存在），不要写入原始响应体；
2. 保留底层异常链，便于日志排查；
3. 预检失败和运行失败使用不同错误码，方便区分。

---

## 6. Phase 10 Review 收口核对

| Phase 10 发现 | 当前状态 | 依据 |
|---|---|---|
| SQLite 标量 / 数组类型误配 | 已收口 | SQL 增加 JSON 类型守卫；数字不会匹配字符串；新增 SQLite 类型与 metacharacter 测试（`app/retrieval/filters.py:109-181`、`tests/test_metadata_filters.py:299-335`） |
| 报告延迟口径不一致 | 已收口 | 明确 pre / during 包含 SQL 预过滤与候选取回；测试校验 `latency_scope`（`docs/METADATA_FILTER.md:118-124`、`tests/test_metadata_filter_report.py:72-77`） |
| dirty 检查忽略 untracked 输入 | 已收口 | 关键路径 untracked 会判定 dirty；仅显式非关键路径豁免并有测试（`tests/test_metadata_filter_experiment.py:59-86`） |
| 负例 FPR 未报告 | 已收口 | 报告增加 4 条不可回答 Query、FPR 与拒答边界说明（`docs/METADATA_FILTER.md:112-116`） |
| under-K 未报告 | 已收口 | manifest 增加 `below_top_k_queries`，报告表显示 pre=15 / post=21（`tests/test_metadata_filter_report.py:84-85`） |
| candidate-k 语义不清 | 已收口 | manifest 增加 `candidate_k_semantics`，报告说明只作用于 RRF 通道深度（`tests/test_metadata_filter_report.py:76-77`） |
| PG / alembic 未复验 | 仍未复验 | 本机 PostgreSQL 仍拒绝连接；`uv run alembic check` 再次失败 |

---

## 7. 纪律与设计 / 开发计划符合性

| 约束 / 计划项 | 结论 | 依据 |
|---|---|---|
| Rerank Pipeline：20 candidates -> Rerank -> Top 5 | 不符合 | 实际只重排 Fusion `top_k=5` 结果；见 2.1 |
| 比较 Hybrid 与 Hybrid + Rerank | 尚未完成 | 单次运行只返回 rrf 通道，且无正式产物 / 报告；见 4.1、4.2 |
| 指标 Recall@5 / MRR / Latency | 部分具备 | 评测器可计算前两项；`rerank_seconds` 与 query latency 已记录，但通道级延迟细节不足 |
| 产出 `docs/RERANK_EXPERIMENT.md` | 未完成 | 文件不存在 |
| Reranker 不新增 / 删除 / 改写候选 | 符合边界 | 身份从原始 Fusion 候选取回，未知 / 重复身份 fail fast（`app/rerank/hybrid.py:19-40`） |
| 阶段边界：不提前接入 `/search` / RAG | 符合 | 本增量未修改公开 search / RAG 契约 |
| OpenAI-compatible 可替换 | 部分符合 | 协议抽象和本地 Key 放宽合理，但成功响应契约过严；见 2.2 |
| 配置同步与 fail fast | 符合 | `rerank_top_n` / timeout / retries 有校验，`.env.example` 更新 |
| 实验隔离 | 符合 | 真实实验要求库名包含 `rerank` 并验证 pgvector |
| 干净实验 / manifest | 尚未适用 | 没有 manifest；脚本已有哈希、脱敏命令、`git_commit_role` 等框架 |

---

## 8. 下一步建议

1. 先修 `rerank_candidates` 的真实候选深度，并增加可证明 Top-20 输入的测试；
2. 修正 rerank 客户端成功契约：允许合法少于 `top_n`，禁止非空输入返回零条；
3. 为 baseline / rerank 设计同 run 双通道或明确 paired-run manifest；
4. 增加模型预检并解决 `Qwen3.5-9B-DFlash` 当前不可用问题；
5. PostgreSQL 恢复后重跑 `alembic check` 与 PG 全量测试；
6. 修正后再从干净工作区生成 manifest 与 `docs/RERANK_EXPERIMENT.md`。
