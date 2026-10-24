# Review：Phase 9 Hybrid Search（含 Phase 8 Review 收口）

- 分支：`phase-9-hybrid`
- 评审时间：2026-10-20 05:32（Asia/Shanghai）
- 评审范围：上次 review（`review/review-phase-8-bm25-20261020-0502.md`，基线
  `567a714`）之后的新增提交
  - `5303668` fix(lexical)：修正 top-k 指标口径与 Review 问题
  - `92bdc84` docs(lexical)：记录 Phase 8 干净实验结果
  - `73e0b9b` feat(hybrid)：实现 Phase 9 Weighted 与 RRF 融合
  - `a73d963` docs(hybrid)：记录 Phase 9 混合检索实验
  - `590a54a` fix(hybrid)：对齐干净实验结果与报告口径
  - `2c179e0` docs(hybrid)：记录 Phase 9 干净实验结果
- 变更规模：`git diff --stat 567a714..HEAD` -> 26 files changed,
  3340 insertions(+), 129 deletions(-)
- 当前 HEAD：`2c179e0f94ffe1786867793c1d5bfd907bc51782`

## 验证结果

以下为本次实际执行并观察到的结果：

- SQLite 默认路径：`441 passed, 9 skipped`
- `uv run ruff check .`：`All checks passed!`
- `uv run alembic check`：`No new upgrade operations detected`
- Phase 9 dry-run：数据集 SHA-256 前缀 `da92624f5f28`、24 条 Query、47 个
  Chunk、`candidate_k=20`、`top_k=10`、RRF 1:1、`rrf_k=60`
- Phase 8 dry-run：通过，输出 `top_k=10`、`k_values=[5, 10]`
- `scripts/hybrid_sweep.py --help`：通过
- 手工探针确认：`evaluate_hybrid(..., fusion="weighted")` 与
  `fusion="rrf"` 均返回相同的四个通道；反转 `ChannelHit.higher_is_better`
  不会改变加权融合结果

未执行 PostgreSQL 全量测试，也未重新运行真实 Embedding / pgvector 实验。
因此本次对 PostgreSQL 服务层 SQL、真实融合结果与 manifest 数值复核的边界是
“代码和 SQLite 已验证、离线重算与报告一致；PG 路径未在本轮实机复验”。

---

## 1. 总体结论

Phase 9 的主设计方向正确，阶段边界也守住了：

- 融合器把 cosine distance 与 BM25 分别处理，没有把两种原始分数直接相加；
  Weighted 在通道内做 min-max，RRF 只使用名次；
- 候选身份保留 `(source_type, source_id, chunk_index)`，避免同来源多 Chunk
  被错误合并；融合结果保留两通道名次、原始分数和 `matched_terms`；
- 实验固定 Phase 8 的 24 条 Query / 47 个 Chunk，Vector 与 BM25 使用同一批
  候选；报告如实记录了小样本、参数扫描过拟合、负样本全部误召回、无 HNSW
  和延迟口径限制；
- 没有提前把 Metadata Filter（Phase 10）、Rerank（Phase 11）或公开 API
  分数契约塞进 Phase 9；`/search` 与 RAG 未被擅自替换，符合开发计划；
- Phase 8 上次 review 指出的 top-k 动态指标、分类表漏检、Defect 数量、
  Unicode 词项、空白 / 未知来源边界和 1,000 Chunk 规模护栏已实质闭环。

但新增实现仍有一个高优先级接口契约问题：**实验脚本的 `--fusion` 并不会限制
实际评测通道**。默认命令因为本来就要比较四个通道，现有报告的数字仍自洽；但
显式传入 `--fusion weighted` 或 `--fusion rrf` 时，CLI、manifest 与真实执行
语义不一致，后续复用该入口做单策略实验会得到看似合法、实则未按参数执行的产物。

此外有若干中优先级问题：融合器忽略自己声明的 `higher_is_better`；生产向量
查询缺少稳定 tie-break；配置中的两个通道 Top-K 没有分别生效；RRF 权重扫描表
未被一致性测试逐行校验；Phase 9 manifest 的提交溯源需要更明确地解释。

总体上，**Phase 9 可以作为 Hybrid 实验接缝进入 Phase 10，但不建议在修正
`--fusion` 契约和上述稳定性问题前，把该接口当作可配置的单策略生产入口。**

---

## 2. 阻断性问题

无。

当前默认参数与默认命令下的实验数字自洽，未发现会让 47 个 Chunk、24 条 Query
的已发布指标错误或让服务立即不可用的缺陷。高优先级问题限制的是实验入口的
可复用性与可审计性，不否定当前默认报告。

---

## 3. 高优先级发现

### 3.1 `--fusion` 被接受并写入 manifest，但没有控制实际评测通道

- 位置：`scripts/hybrid_experiment.py:278`、`scripts/hybrid_experiment.py:354-368`、
  `app/evaluation/hybrid_experiment.py:190-226`
- CLI 接受 `--fusion weighted|rrf`，随后把它传给 `evaluate_hybrid(...)`；
- `evaluate_hybrid()` 声明 `fusion` 参数，但函数体内从未读取它。无显式
  `variants` 时，永远评测：
  - `vector`
  - `keyword`
  - `weighted`
  - `rrf`
- 本次直接探针确认，`fusion="weighted"` 与 `fusion="rrf"` 返回的通道名称
  完全相同；dry-run 也只回显参数，不能暴露该差异；
- manifest 仍记录 `retrieval.fusion`，见 `results/hybrid-phase9.json:50-57`。

**影响**

当前默认命令旨在“比较四个通道”，所以已发布报告没有因此产生错误数字。但接口
承诺与执行行为不一致：

- 用户运行 `--fusion weighted` 时，会同时执行并输出 RRF，且无法知道
  `fusion` 未生效；
- manifest 的 `retrieval.fusion=rrf` 容易被读成“本次只运行 RRF”，而结果中
  同时有 Weighted 和单通道结果；
- 后续复用该入口做单策略复验、参数消融或 CI 校验时，可能把未执行所选策略的
  结果当作该策略的产物。

这是静默配置失效，不是单纯的文档措辞问题。

**建议**

1. 若实验设计是“一次比较四个通道”，删除 `--fusion`，或改名为
   `default_fusion` / `reported_fusion`，并在 manifest 中记录实际 `variants`；
2. 若 `--fusion` 应选择策略，则让 `evaluate_hybrid()` 用它构造 `variants`，
   例如只评测选定融合通道（并按需保留单通道基线）；
3. manifest 增加 `variants` / `evaluated_strategies`，并断言它们与 CLI
   选择一致；
4. 增加测试：分别以 `weighted` 和 `rrf` 运行入口，断言通道集合不同且与
   manifest 一致。

---

## 4. 中优先级发现

### 4.1 `ChannelHit.higher_is_better` 契约被忽略，Weighted 硬编码分数方向

- 位置：`app/retrieval/hybrid.py:18-33`、`app/retrieval/hybrid.py:130-146`
- `ChannelHit` 文档明确说“融合器负责按 `higher_is_better` 归一化”，
  `channel_hits()` 也公开暴露这个参数；
- Weighted 分支却固定对向量使用 `higher_is_better=False`，对关键词使用
  `True`，完全没有读取每个 `ChannelHit.higher_is_better`；
- 本次探针反转两个通道的 `higher_is_better` 后，分数与排名仍由硬编码方向
  决定；若向量方向反转，最小距离反而得到最高归一化分。

**影响**

当前标准适配层恰好产生“向量低优、BM25 高优”，所以已发布实验不会因此错误。
但公开的融合 API 不遵守自己的字段契约：

- 未来接入第三个通道、替换为 similarity 分数、或调用方传入已按相反方向
  预处理的候选时，会静默按错误方向归一化；
- 反向输入会使最小距离得到最高分，属于难以从结果中发现的排序错误；
- 测试已有 `higher_is_better` 构造参数，却没有验证它真正控制归一化。

**建议**

- 在 Weighted 中按每个 `ChannelHit.higher_is_better` 归一化；同一通道内若
  方向不一致，应显式拒绝；
- 或缩小并修正契约：明确 `fuse()` 只接受“向量距离 / 关键词分数”两个固定
  通道，把 `higher_is_better` 从泛化接口中移除，不要保留无效字段；
- 增加反向方向、混合方向与错误方向的回归测试。

### 4.2 生产向量查询缺少稳定 tie-break，实验路径已有而服务路径没有

- 位置：`app/services/knowledge_chunk_service.py:176-183`
- 实验路径 `app/evaluation/hybrid_experiment.py:124-135` 使用
  `distance, source_type, source_id, chunk_index` 排序；
- 服务层 `KnowledgeChunkService.search()` 只按 `distance` 排序；
- 当 embedding 相同、距离一致或浮点值相等时，SQL 未保证返回顺序稳定。

**影响**

RRF 使用通道名次而不是原始分数。向量候选名次不稳定会直接改变 RRF 的
`1/(k+rank)` 分量，使同一 Query 在重复执行或不同执行计划下得到不同融合顺序。
当前 47 个 Chunk 的实验不一定触发该路径，但生产接缝不能依赖“恰好没有平局”。
这与 Phase 9 自己强调的稳定 tie-break 原则不一致。

**建议**

- 在服务层向量查询中加入 `source_type, source_id, chunk_index`（必要时再加
  `id`）作为次级排序；
- 增加 PostgreSQL 测试：插入两个相同 embedding 的 Chunk，反复查询并断言
  顺序一致；
- 若未来 HNSW 的近似排序使精确 tie-break 成本过高，也应在接口层定义可复现
  的稳定规则，而不是依赖数据库默认顺序。

### 4.3 两个通道的候选深度配置没有分别生效

- 位置：`app/services/knowledge_chunk_service.py:273-303`
- 配置同时提供 `retrieval_vector_top_k` 与 `retrieval_keyword_top_k`；
- `hybrid_search()` 却取二者最大值作为单一 `candidate_depth`，并把这个值同时
  传给向量检索和 BM25；
- 当两个配置不相等时，较小通道会被扩大到较大值，配置语义失真。

**影响**

默认两者都是 20，当前 manifest 不受影响。但报告和 README 已把两个参数描述为
可独立配置的候选深度；一旦用户设为例如向量 20、关键词 50，实际会执行
20 / 50 而不是 20 / 20 或配置所期望的深度，进而改变 RRF 名次、Weighted
归一化范围和延迟。该问题不会被当前“candidate_k=20”测试发现。

**建议**

- 分别计算 `vector_candidate_k` 与 `keyword_candidate_k`，并分别传给
  `search()` 与 `lexical_search()`；
- 或承认当前只有一个共享候选深度，合并配置项并在文档 / manifest 中明确；
- 增加两值不相等时的服务层测试，断言两个通道收到的 limit 各自正确；
- 同时给 `retrieval_*` 配置增加正数校验；当前 `Settings` 接受 0，
  服务层只能在部分调用路径报错。

### 4.4 Phase 9 manifest 的提交溯源需要明确，不能只写 `git_commit` + clean

- 位置：`results/hybrid-phase9.json:5-13`、
  `results/hybrid-sweep-phase9.json:6-14`、
  `docs/HYBRID_SEARCH_EXPERIMENT.md:253-257`
- 两个 manifest 都记录 `git_commit=a73d963`、`git_dirty=false`，命令输出到
  `/tmp/...`；它们实际在后续提交 `2c179e0` 才进入仓库；
- `a73d963` 是文档提交，Phase 9 功能位于其前一提交 `73e0b9b`；当前实现与
  报告一致性检查落点又是 `590a54a` / `2c179e0`；
- 报告要求“已提交的 manifest 必须来自 `git_dirty=false` 的同一提交”。

**影响**

这不等同于指标错误：manifest 是干净 `a73d963` 上生成、之后才提交产物，且
功能代码在 `a73d963` 与 `2c179e0` 之间没有变化；因此现有数字可以复现。
但只读 manifest 的人无法区分：

- 功能代码提交；
- 报告 / 测试对齐提交；
- 产物入库提交。

报告里的“同一提交”措辞也容易被理解为“manifest 必须与引用它的报告同在一个
commit”，而当前事实并非如此。这属于审计链表达不足，不是已发生的数据漂移。

**建议**

- 在 manifest 中增加 `code_commit`（功能实现）、`result_commit`（产物入库）
  或 `generated_at_commit`，并让报告明确每一层的含义；
- 或将报告 / manifest 作为同一提交的原子产物重新生成并提交；
- 在报告中写明当前实例：“在干净 `a73d963` 生成，随后在 `2c179e0` 入库”，
  避免读者把“同一提交”理解成产物和引用报告必须在同一个 commit。

### 4.5 Phase 8 的 1,000 Chunk 测试只能证明“未严重退化”，不能证明服务容量

- 位置：`tests/services/test_knowledge_chunk_service.py:113-137`、
  `docs/LEXICAL_SEARCH_EXPERIMENT.md:203-207`
- 测试只断言 1,000 个合成 Chunk 的查询耗时小于 20 秒，且未记录环境基线、
  数据分布或分位数；
- 报告已经正确标注“不是生产容量结论”，这一边界做得好；
- 但 Phase 9 的 Hybrid 服务层会同时执行向量查询 + BM25 全量读取 + 融合，
  当前没有对应的端到端规模测试。

**影响**

20 秒上限可以防止极端退化，但不足以支撑“Hybrid 接缝可在更大库使用”的
判断；尤其 BM25 每次查询仍全量加载并建索引。当前报告没有把它包装成生产
结论，因此这是测试覆盖与后续接入风险，不是本次指标错误。

**建议**

- 保留宽松护栏，但明确其为 smoke bound；
- Phase 10 / 11 接入前补 Hybrid 端到端 P50 / P95、候选数、内存与并发测试；
- 若短期不做大库优化，在接口注释和 handover 中继续标注“仅小规模知识库”。

### 4.6 参数敏感性测试只校验 RRF 行数，没有校验权重变化的指标

- 位置：`tests/test_hybrid_report.py:126-154`
- Weighted 的五行会逐行与 manifest 比较 Recall@5 / MRR；
- 对 RRF 的 2:1、1:2、3:1、1:3 行，测试只执行
  `observed_rrf += 1`，最后断言 `observed_rrf == 5`，没有比较这些行的
  Recall@5 / MRR；
- `CHANGELOG.md:24-26` 和报告把该测试描述为参数敏感性数字的一致性护栏。

**影响**

当前 `docs/HYBRID_SEARCH_EXPERIMENT.md:172-176` 中的 RRF 权重扫描数字与
manifest 一致；但未来报告漂移时测试仍会通过。这里恰好包含报告重点讨论的
“1:2 RRF MRR 最高”结论，因此计数检查不足以支撑结论的可追溯性。

**建议**

- 与 Weighted 一样，解析 `方法 / vector:keyword / k` 并从 manifest 取对应项，
  逐行比较 Recall@5 与 MRR；
- 明确 RRF 1:1 / k=60 在“权重表”和“k 扫描表”中各有一次引用，避免用行数
  代替数值校验；
- 增加一个故意篡改 1:2 行指标的负向测试，确认一致性测试会失败。

---

## 5. 低优先级 / 一致性观察

- **L1 `docs/handover.md` 的 evaluation 目录说明漏了 Hybrid。**
  `docs/handover.md:66` 仍写“Chunking / Embedding / Lexical 实验”，但目录中
  已有 `hybrid_experiment.py`；补上 Hybrid 可降低接手成本。
- **L2 报告中的 `--fusion` 描述应随实现修正。**
  当前报告主要描述比较四通道，但 README / 脚本提供 `--fusion`；在修复
  3.1 后，应同步说明该参数是“默认展示策略”还是“唯一评测策略”。
- **L3 实验 manifest 的命令路径指向 `/tmp`。**
  `results/hybrid-phase9.json:12-13` 与
  `results/hybrid-sweep-phase9.json:13-14` 记录的 `--output` 不是仓库中的
  最终路径。数值不受影响，但复现者不能直接照命令重放产物；建议记录为
  `--output results/...`，或额外记录产物入库路径。
- **L4 融合参数没有拒绝 NaN / Infinity。**
  `fuse()` 只检查权重 `< 0`、`rrf_k < 1`，没有检查有限数；极端输入可能产生
  非预期排序。当前 CLI 的正常数值不会触发，属于 API 防御性缺口。
- **L5 Hybrid 测试的回归强度还有提升空间。**
  `tests/test_hybrid_experiment.py:86-105` 的“not raw added”测试只断言指标
  非负且候选数大于 0，不能证明归一化与权重真正生效；建议用固定小语料断言
  具体顺序和分数。
- **L6 README / handover 不再写死测试总数。**
  这是正确的改进；本次实测为 441 passed + 9 skipped，若未来要恢复数字，
  应由 CI 生成而不是手工维护。
- **L7 Hybrid 实验没有复用 Phase 8 的动态 K 规则。**
  `app/evaluation/hybrid_experiment.py:230` 只保留 5 / 10，`--top-k 7` 不会
  输出 Recall@7，`--top-k 20` 也不会输出 Recall@20。当前默认 `top_k=10`
  不受影响，但应与 `k_values_for()` 的口径统一，并补 7 / 20 的测试。

---

## 6. 上次 Review 问题闭环

| 上次问题 | 状态 | 本次依据 |
|---|---|---|
| 3.1 `--top-k` 可变但指标固定 Recall@5/10 | 已闭环 | `k_values_for()` 动态生成；测试覆盖 1 / 5 / 7 / 10；manifest 记录 `top_k` / `k_values` |
| 4.1 Phase 8 manifest 指向旧提交且 dirty | 已闭环 | `results/lexical-phase8.json:5-6` 为 `5303668`、`git_dirty=false`；命令直接写仓库结果路径 |
| 4.2 分类表漏检 `exact_priority` | 已闭环 | 正则接受加粗 / 非加粗，并断言分类集合等于 manifest 分类全集 |
| 4.3 Defect 数量表述错误 | 已闭环 | 报告、数据集描述、handover 均改为“复用 5 + 新增 BUG-1234 => 共 6” |
| 4.4 `matched_terms` 可解释性有限 | 已闭环（文档 + 测试） | 明确是 tokenized terms；中文命中形态有固定测试 |
| 4.5 全量加载 / 建索引未验证规模 | 部分闭环 | 增加 1,000 Chunk 宽松护栏；仍不是生产容量与 Hybrid 端到端验证 |
| 5.x 文档测试数与目录说明 | 大部分闭环 | README / handover 不再写死总数；Hybrid 目录说明仍有小遗漏（L1） |

Phase 8 的数据集 SHA-256 当前为
`da92624f5f287fcc83943fb68ebc6d969d6d71b35e5b519215c687606e996150`，
与报告、manifest、Phase 9 复用值一致；47 个 Chunk 的身份键无重复，
来源构成为 12 TestCase / 6 Defect / 5 Requirement / 24 Document Section。

---

## 7. 纪律与设计符合性

| 检查项 | 结论 | 说明 |
|---|---|---|
| 阶段边界 | 符合 | 未提前接入 Metadata Filter / Rerank；`/search` 与 RAG 未替换 |
| Evidence First | 基本符合 | 报告明确小样本、负样本误召回、无 HNSW；问题在于 `--fusion` 会产生误导性实验配置 |
| 数据隔离 | 符合 | 真实 Hybrid 只允许库名含 `hybrid` 的 PostgreSQL；不写生产表 |
| 可复现性 | 基本符合 | 数据集 / 候选哈希、模型元数据、脱敏命令齐全；commit 语义与 `/tmp` 命令路径仍需收口 |
| 实验变量控制 | 符合 | 固定同一 Chunk、同一 Embedding 与 BM25，比较四个通道 |
| 指标口径 | 符合 | 默认 `top_k=10`，Recall@5/10、MRR 与报告一致；Phase 8 动态 K 已修复 |
| 融合数学 | 基本符合 | Weighted / RRF 公式正确；`higher_is_better` 契约未落地 |
| 稳定性 | 部分符合 | 融合器自身有稳定 tie-break；生产向量 SQL 没有 |
| 测试纪律 | 基本符合 | 全量测试与静态检查通过；`--fusion`、分数方向、SQL tie-break 与 RRF 权重表仍缺反例 |
| 文档一致性 | 基本符合 | Phase 8 数字有完整守护；Phase 9 总体 / 负样本有守护，RRF 权重扫描未逐值校验 |

---

## 8. 建议修复顺序

1. **先修 `--fusion` 契约**：明确它是默认策略还是唯一策略，并让 CLI、
   `evaluate_hybrid()`、manifest 与测试一致。
2. **修 Weighted 的 `higher_is_better`**：按字段归一化或删掉无效泛化契约。
3. **给生产的向量 SQL 加稳定 tie-break**，并补相同 embedding 的 PG 测试。
4. **分别使用 vector / keyword 候选深度**，补齐配置正数校验。
5. **统一 Hybrid 实验的 K 值规则，并补 RRF 权重扫描逐值断言**，避免实验入口
   再次出现指标口径与报告测试盲区。
6. **增强 manifest 溯源与命令路径**，让报告中的“同一提交”有唯一、可执行的
   含义。
7. 在进入 Phase 10 前补 Hybrid 端到端性能 / 候选规模测试，继续保留“小规模
   实验接缝”的边界声明。

完成 1–3 后，Phase 9 的接口契约和排序稳定性才适合作为 Phase 10 Metadata
Filter 的可靠基线。
