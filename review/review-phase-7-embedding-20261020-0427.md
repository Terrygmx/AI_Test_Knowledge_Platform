# Review：Phase 6 修复 + Phase 7 Embedding 模型对比

- 分支：`phase-7-embedding`
- 评审时间：2026-10-20 04:27（Asia/Shanghai）
- 评审范围：上次 review（`review/review-phase-6-chunking-20261020-0347.md`，基线 `18ca697`）之后的新增提交
  - `2075d18` fix(chunking)：修复 Phase 6 语义切分与实验可复现性
  - `807d208` feat(embedding)：完成 Phase 7 Embedding 模型对比实验
  - `b67f2e7` fix(embedding)：让 Phase 7 实验可在同一隔离库重复运行
  - `9ab3ab8` docs(embedding)：记录 Phase 7 干净提交实验结果
- 变更规模：`git diff --stat 18ca697..HEAD` → 19 files changed, 1816 insertions(+), 178 deletions(-)
- 复验结果：
  - SQLite 默认：`355 passed, 7 skipped`
  - 真实 PostgreSQL + pgvector（`aitkp_test`）：`361 passed, 1 skipped`
  - `uv run ruff check .` 全绿；`uv run alembic check` 无漂移
  - `scripts/chunking_experiment.py --dry-run` 与 `scripts/embedding_experiment.py --dry-run` 均通过，数据集 SHA-256 `63f0f6a4...` 与两份 manifest 一致
  - **真实重跑 Phase 7**（`aitkp_embedding` + OMLX）结果与 `docs/EMBEDDING_EXPERIMENT.md` 完全一致：R@5 0.966 / 0.941 / 0.466，MRR 0.936 / 0.934 / 0.447；同库连续运行两次成功
  - 手工复算 Phase 6：fixed 30/27/25/25、semantic 43、entity+heading 46，与新版 manifest 一致；semantic 最小长度 40、最大 97，不再「一句一 Chunk」
- 工作区：无未提交业务改动（`eval/fixtures/.gitkeep` 为空占位，另有 4 个历史 review 文件）

---

## 1. 总体结论

这次增量分两部分，两部分都做得很实在。

**上一轮 Phase 6 的 5 个问题全部闭环**，而且不是「改数字对齐」而是改根因：

- semantic 不再用共享一半文本的滑动窗口，改为直接比较相邻句子，并加 `min_chars=40` 的短组合并；
- `index_seconds` 拆成 `chunking_seconds` / `index_seconds` / `total_setup_seconds`，并记录切分阶段与索引阶段的 Embedding 调用次数 / 文本条数；
- `start/end` 改名 `span_start/span_end`，overlap 与标题路径单独放 metadata；测试直接断言 `document.content[span_start:span_end] == content`；
- manifest 补了 `git_commit` / `git_dirty` / 起止时间 / 脱敏命令 / embedding 地址；
- 策略改名 `entity+heading`，fixed-1200 标了「未触发」，semantic 阈值与最短长度做了 CLI 覆盖。

**Phase 7 的工程设计与实验纪律也在线**：

- 固定 Phase 6 的 `entity+heading` 基线，只替换 Embedding，归因干净；
- 不同维度模型各建独立 pgvector 表，不截断 / 不补零，这是「不伪造可比性」的正确做法；
- 实验库名保护（必须含 `embedding`）、pgvector 扩展检查、默认 dry-run、重复运行清理，一个不少；
- 报告把「bge-m3 与 embeddinggemma 差距不显著」「46 行上的延迟没有工程意义」「表未建 HNSW」「距离不可跨模型复用」都写清楚了，没有把 0.966 包装成生产结论；
- 我按报告命令重跑了一遍真实实验，三项指标逐项一致，且同库二次运行不冲突。

但 Phase 7 本身有两个中优先级问题值得在进入 Phase 8 前处理：

1. **manifest 的 `git_commit=b67f2e7` / `git_dirty=false` 只能证明「这份 JSON 是在那次运行生成的」，不能证明「这份 JSON 被记录进仓库的提交也是干净的」**——`9ab3ab8` 只改了 `results/embedding-phase7.json`，没有同步改 `docs/EMBEDDING_EXPERIMENT.md` 中的延迟数字；报告的延迟表与 manifest 已经不一致（见 §4.1）。
2. **Embedding 实验没有记录服务端模型版本 / 运行参数**，当前只记了 base URL 和模型名。OMLX 模型名相同但量化版本 / 服务配置变化时，相同命令未必得到相同向量；报告已说阈值要重校，但 manifest 无法回答「当时到底是哪一版服务」。

另外，Phase 6 的分析文字现在有一处与新数据不一致：`docs/CHUNKING_EXPERIMENT.md` §3 新增了「fixed-1200 在本语料没有触发」的正确说明，但 `docs/PROJECT_SCOPE.md` 的 Phase 6 行仍写「实体级」而没同步新策略名；这属于低优先级一致性。

---

## 2. 阻断性问题

无。

---

## 3. 高优先级发现

无新增高优先级问题。

上一轮 Phase 6 的唯一高优先级问题（semantic 过切 + manifest 不可复现）已修复；本次真实复跑 Phase 7 指标与报告一致，没有出现「报告数字与代码不符」的同类问题。

---

## 4. 中优先级发现

### 4.1 Phase 7 报告的成本 / 延迟表与 manifest 不一致

- 位置：`docs/EMBEDDING_EXPERIMENT.md:90-100`、`results/embedding-phase7.json:55-142`
- 报告表格写的是：
  - bge-m3：内容 Embedding `1.159s`、写库 `0.040s`、索引合计 `1.199s`
  - embeddinggemma：`0.614s` / `0.034s` / `0.648s`
  - all-MiniLM：`0.268s` / `0.021s` / `0.289s`
- 当前仓库里的 manifest 是 `9ab3ab8` 更新后的第二次干净运行，实际值为：
  - bge-m3：`1.106s` / `0.034s` / `1.141s`
  - embeddinggemma：`0.413s` / `0.028s` / `0.441s`
  - all-MiniLM：`0.136s` / `0.018s` / `0.154s`
- 也就是说报告保留的是**第一次运行**（`b67f2e7` 之前那次）的延迟，而 `results/embedding-phase7.json` 已被替换为**第二次运行**。指标逐项一致，但成本数字不一致。
- 我刚按报告命令重跑了一次，得到的又是另一组延迟（bge 3.3s / gemma 1.5s / minilm 0.5s），说明本机 OMLX 的绝对耗时有明显波动——这更说明报告应使用 manifest 中的同一组数字，并明确「只作同环境相对参考」。
- 建议：以 `results/embedding-phase7.json` 为唯一事实源，更新报告表格；或者在 manifest 中新增 `report_commit` / `result_id`，让报告能指向具体一次运行。文档里不要再保留无法与 manifest 对应的数字。

### 4.2 Phase 7 manifest 缺少服务端模型版本与批次信息

- 位置：`scripts/embedding_experiment.py:_manifest`、`app/evaluation/embedding_experiment.py:evaluate_embedding`
- 当前记录：`embedding_base_url`、三个模型的 `name` / `model` / `dimension`、数据库名、数据集 SHA、Chunk 统计、分段延迟与指标。
- 未记录：
  - OMLX 服务进程版本 / 模型文件 hash / 量化格式（`-4bit` / `-oQ8e` 只写在模型名里）；
  - `embedding_batch_size`（当前 32）与 `max_retries` / `timeout`；
  - 每次 `embed_texts` 的实际批次数量（46 条 / 32 = 2 批，但 manifest 只有总耗时）。
- 影响：模型名相同、服务端换了量化版本或 batch 配置时，manifest 无法解释「为什么同一命令结果不同」；Phase 8 之后如果换 Embedding 服务，历史结果不可审计。
- 建议：manifest 增加 `embedding_batch_size`、`embedding_timeout_seconds`、`batch_count`，并尽可能记录服务端 `/models` 返回的模型元数据（不要求一定有）。

### 4.3 Phase 7 用 `entity+heading` 作为「最优基线」的理由，依赖的是同一批 40 条 Query 上的 Phase 6 指标

- 位置：`docs/EMBEDDING_EXPERIMENT.md:20-30`
- 报告写：「Phase 6 在相同 40 条 Query 上，semantic R@5 0.975 / MRR 0.919，entity+heading R@5 0.966 / MRR 0.936；entity+heading MRR 更高、成本更低，所以固定它」。
- 逻辑本身没毛病，但这等于**用同一评测集同时选 Chunking 基线和评估 Embedding**，存在轻微的选择偏差：`entity+heading` 之所以被选中，部分原因就是它在这 40 条 Query 上 MRR 最高。Phase 7 的模型排名因此是在「对 entity+heading 友好的 Query 集」上得到的。
- 报告 §5 已写「不能外推到生产规模」，但没有点明「基线选择与评测集重合」这一层。
- 建议：在 §2 或 §7 补一句「Phase 6 与 Phase 7 共用同一 Gold Set，基线选择本身也受该集合影响；扩大到 100 条后应重新确认基线与模型排名」。这不是要求现在换基线，只是把归因边界写清楚。

### 4.4 `evaluate_embedding` 在每次模型评测时 `drop/create` 表，但没有清理其他模型的表

- 位置：`app/evaluation/embedding_experiment.py:179-181`
- 每个模型独立表是对的；但脚本只 drop 当前模型的表，如果从 3 个模型改成 1 个模型重跑，旧模型的 `phase7_embedding_*` 表会留在隔离库里。报告说「三张表各 46 行」，但没有「本次运行只应存在这几张表」的断言。
- 影响：隔离库长期使用后会积累历史模型表，后续查询 / 备份 / 清理容易混淆；如果有人误把实验库当配置库，旧表可能被当成有效数据。
- 建议：运行开始时列出并清理 `phase7_embedding_%` 前缀的旧表（或显式 `--keep-old-tables`），并在 manifest 记录本次创建的表清单。

### 4.5 `_client_for` 通过 `Settings(_env_file=None, ...)` 复制配置，但没有复制 `embedding_batch_size` 之外的相关参数

- 位置：`scripts/embedding_experiment.py:_client_for`
- 当前显式复制了 `embedding_base_url` / `api_key` / `model` / `dim` / `batch_size`。如果以后给 `OpenAICompatibleEmbeddingClient` 增加 `timeout` / `max_retries` / 代理等参数，实验客户端会静默使用默认值，而不是生产配置。
- 影响：Phase 7 的「调用成本」可能和生产客户端不一致，且这种不一致不会报错。
- 建议：改用 `settings.model_copy(update={...})`（或 `dataclasses.replace` 风格）复制全部字段，只覆盖 model / dim，避免漏字段。

---

## 5. 低优先级 / 一致性观察

- **L1 `docs/PROJECT_SCOPE.md:137` 的 Phase 6 描述仍是「固定 / 语义 / 实体级 Chunk 对比」**，没有同步 `entity+heading` 新命名；建议与 README / CHUNKING_EXPERIMENT 对齐。
- **L2 `docs/handover.md:68` 写 `tests/ # 346 个通过用例`**，但本次实测 SQLite 是 `355 passed`。第 121 行已更新为 355，目录树注释残留旧数字。建议统一为「以当前实测为准」或 355。
- **L3 `docs/CHUNKING_EXPERIMENT.md` §4.1 的耗时数字与 `results/chunking-2026-10-20.json` 也不同**（报告 3.02/6.98/9.58/10.59/4.36+3.51/3.30，manifest 中为 3.024/6.979/9.580/10.589/4.360+3.509/3.298，差异在四舍五入范围内，可接受）；但建议在文档中明确「数字四舍五入到 0.01s，以 manifest 为准」。
- **L4 `docs/EMBEDDING_EXPERIMENT.md` §4.3 的「Query Embedding P50/P95」与 manifest 第二次运行不一致**（报告 0.024/0.028，manifest 0.0246/0.0313）；与 §4.1 同源，修 §4.1 时一并处理。
- **L5 `README.md:154` 的 Phase 7 命令只给 `--dry-run`**，真实命令在 `docs/EMBEDDING_EXPERIMENT.md` §6；建议 README 补一行真实命令（或明确「完整命令见报告」），减少接手者找不到入口。
- **L6 `tests/test_embedding_experiment.py` 的 manifest 单测用 `_manifest(...)` 直接传入构造的 `EmbeddingResult`**，没有断言「报告与 manifest 的数字一致」；这类文档 / 结果漂移只能靠人眼。建议加一个轻量脚本或测试，解析 `docs/EMBEDDING_EXPERIMENT.md` 的表格并与 `results/embedding-phase7.json` 对比（至少比对 R@5 / R@10 / MRR 三个值）。
- **L7 `eval/fixtures/.gitkeep` 与 `eval/.gitkeep` 同时存在**，属于无害冗余；可以删掉子目录占位或在 README 说明 fixtures 由数据集填充。

---

## 6. 上次 review 问题闭环情况

| 上次问题 | 状态 | 依据 |
|---|---|---|
| §3.1 semantic 严重过切 / manifest 不一致 | 已修复 | `SemanticChunker` 改为相邻句子比较 + `min_chars=40` 短组合并；手工复算文档 Chunk 23（总 43），与新版 manifest 一致；`test_semantic_document_chunks_stay_at_section_granularity` 断言不超过 Section 数 |
| §4.1 `index_seconds` 不含切分阶段 Embedding | 已修复 | 新增 `chunking_seconds` / `total_setup_seconds` 与两阶段调用计数；报告表格拆分「切分耗时 / 索引耗时 / 总准备耗时」；`test_run_experiment...` 断言 `total_setup_seconds = chunking + index` |
| §4.2 `start/end` 与 content 不一致 | 已修复 | 改名 `span_start/span_end`，overlap 与 `heading_path` 独立 metadata；测试断言 entity 的 `document.content[start:end] == content`，fixed 的 `content` 以 span 文本结尾 |
| §4.3 段落边界 / 列表不友好 | 部分修复 | 报告 §7 新增「Semantic 对 Markdown 列表、表格和代码块不友好」；实现仍是逐句切分，未把列表 / 代码块视为不可分单元。作为实验局限可接受，但 Phase 8 前建议至少加一条测试 |
| §4.4 manifest 缺运行信息 | 已修复 | Phase 6 / 7 manifest 均记录 `git_commit` / `git_dirty` / 起止时间 / 脱敏命令 / embedding 地址；Phase 6 还记录策略配置与调用计数 |
| §4.5 entity 命名不对等 | 已修复 | 统一改名为 `entity+heading`，Phase 7 也沿用该基线 |
| L1 README 未完成项矛盾 | 已修复 | README 未完成项改为「将 Gold Set 扩大到约 100 条人工确认 Query 后复验 Recall@K / MRR」 |
| L2 来源声明 | 已修复 | CHUNKING_EXPERIMENT §2.1 改为「仅参考字段组织」 |
| L3 SQLite 编排测试空 Query | 部分修复 | 仍用 `queries=[]`，但新增 `chunking_seconds` / 调用计数 / `total_setup_seconds` 断言；检索指标路径仍只由纯逻辑测试覆盖 |
| L4 fixed-1200 未触发 | 已修复 | 报告表格标「fixed-1200（未触发）」，§3 明确最长文档 655 字符 |
| L5 慢线程测试耗时 | 未修（可接受） | 仍是 2s sleep；全量 4.92s，影响很小 |
| L6 semantic 阈值无 CLI | 已修复 | `--semantic-threshold` / `--semantic-min-chars` 已接入；`test_fixed_strategies_use_documented_overlap` 断言默认 0.55 / 40 |
| L7 manifest 无 git 信息 | 已修复 | 同上 |
| 上一轮 RAG 4.1–4.4 / M1–M3 | 未回退 | `8730286` 的修复仍全绿；本次 Phase 7 未触碰 RAG 运行时 |

---

## 7. 纪律符合性（本次增量）

| 纪律 | 结论 | 依据 |
|---|---|---|
| TDD 先红后绿 | 符合 | Phase 6 修复新增 span / 调用计数 / manifest / semantic 粒度测试；Phase 7 新增 `test_embedding_experiment.py`（199 行）覆盖模型参数解析、隔离库、维度表、manifest 脱敏、同库重复运行 |
| 测试不依赖 Docker / 外部 API | 符合 | 355 passed 全在 SQLite；Embedding 实验逻辑用 Fake / 注入；真实 OMLX 只用于手工复跑 |
| PG 专属能力有实机验证 | 符合 | PG 361 passed；真实 Phase 7 在 `aitkp_embedding` + pgvector 上重跑，三模型指标一致 |
| Evidence First | 符合 | 报告逐项区分「指标一致」与「延迟波动」；明确 46 行无 HNSW、距离不可跨模型复用；不可回答 Query 误召回率没有洗白成阈值 |
| LLM / Embedding 可替换 | 符合 | `EmbeddingClient` Protocol 未变；Phase 7 只注入 client，不改业务检索路径 |
| 不用 LangChain / LlamaIndex | 符合 | 无新依赖，实验编排手写 |
| 实验隔离 | 符合 | 库名必须含 `embedding` / `chunking`；默认 dry-run；PG + vector 扩展检查；重复运行清理基线实体 |
| 统一错误信封 | 未涉及 | Phase 7 是离线脚本，失败走 `ValueError` / `SystemExit`，不经过 HTTP；上一轮 429 / 503 语义未回退 |
| 约定式提交 + CHANGELOG | 符合 | `fix(chunking):` / `feat(embedding):` / `fix(embedding):` / `docs(embedding):`；CHANGELOG 有 Phase 6 修复与 Phase 7 条目 |
| 欠账显式记录 | 部分 | 报告已写小语料、无 HNSW、阈值需重校、列表 / 表格不友好；但「报告延迟表与 manifest 不一致」（§4.1）和「基线选择与评测集重合」（§4.3）未记录 |
| 决策原因写进 docstring | 符合 | `SemanticChunker` / `EmbeddingConfig` / `evaluate_embedding` / `_client_for` / `_manifest` 均有取舍说明 |

---

## 8. 建议的下一步（按优先级）

1. 修 §4.1 / L4：以 `results/embedding-phase7.json` 为唯一事实源更新 `docs/EMBEDDING_EXPERIMENT.md` 的耗时 / Query 延迟表；或给 manifest 加 `result_id` 并让报告引用它。
2. 修 §4.2：manifest 补 `embedding_batch_size`、`batch_count`、`timeout` / `max_retries`，并尽量记录服务端模型元数据。
3. 修 §4.3：在报告 §2 / §7 明确「Phase 6 基线选择与 Phase 7 评测共用同一 Gold Set」的归因边界。
4. 修 §4.4 / §4.5：实验开始清理旧 `phase7_embedding_%` 表；`_client_for` 用完整 Settings 复制而不是逐字段拷贝。
5. 低优先级：L1 `PROJECT_SCOPE` 同步 `entity+heading`；L2 handover 目录树 346→355；L5 README 补真实实验命令；L6 加「报告表格 vs manifest」一致性测试。
