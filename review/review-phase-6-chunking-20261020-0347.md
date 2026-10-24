# Review：Phase 6 Chunking 对比实验（含上一轮 Review 修复）

- 分支：`phase-6-chunking`
- 评审时间：2026-10-20 03:47（Asia/Shanghai）
- 评审范围：上次 review（`review/review-phase-3-llm-20261020-0242.md`）之后的新增提交
  - `8730286` fix(review)：拆分 RAG 满载语义并完善补向量与空文档边界
  - `18ca697` feat(chunking)：完成 Phase 6 Chunking 对比实验
- 变更规模：`git diff --stat 3dcc7fb..HEAD` → 32 files changed, 3208 insertions(+), 166 deletions(-)
- 复验结果：
  - SQLite 默认：`339 passed, 6 skipped`
  - 真实 PostgreSQL + pgvector（`aitkp_test`）：`344 passed, 1 skipped`
  - `uv run ruff check .` 全绿；`uv run alembic check` 无漂移
  - `scripts/chunking_experiment.py --dry-run`：数据集 SHA-256 `117b9e154f29...`、40 Query、6 策略，与报告一致
  - 手工复算：按同一策略配置构建全部 Chunk，fixed 系列 30/27/25/25 与 entity 46 与 manifest 完全一致
  - 手工复算：`semantic` 实际生成 **78** 个 Chunk（实体 22 + 文档 56），而报告 / manifest 记录为 **43**
- 工作区：无未提交业务改动（`eval/fixtures/.gitkeep` 为空白占位，另有 3 个历史未跟踪 review 文件）

---

## 1. 总体结论

先说结论：**上一轮 review 的 4 个中优先级问题全部真正闭环**，而且修法比要求更彻底——RAG 满载从「同一 503 靠文案区分」改成 `429 rag_overloaded` + `Retry-After`；后台任务从「共享请求 Session」改成「守护线程 + 自己开会话」；补向量从「一次全量 commit」改成「主键游标分页 + 分批提交」；document 从「只能补空缺」补上了 `--rebuild` 全量重算。这些都配了能失败的测试，不是只改注释。

Phase 6 的整体工程骨架也值得肯定：

- Gold Set 的粒度设计是对的：**`source_type + source_id + 原文证据片段`，不绑定 Chunk ID**，切分策略变化后仍可复用；这是 Chunking 实验能横向比较的前提；
- 数据集有严格校验（可回答问题必须有证据、不可回答问题不能有证据、引用的来源必须存在、query_id 不能重复），并且有 SHA-256 规范化；
- 隔离库保护（库名必须包含 `chunking`、拒绝 SQLite、真实实验检查 pgvector 扩展）比一般「实验脚本」认真得多；
- 报告明确记录了「fixed-800 与 fixed-1200 完全相同」「6 条负样本全部误召回」「小语料不能外推」，没有把 Top-K 非空当证据充分，Evidence First 纪律保持了。

但 Phase 6 有一个**必须先修的高优先级问题**：报告的核心结论建立在 semantic 的指标上，而 semantic 的实际 Chunk 数与记录值不一致，且按当前实现会严重过切。这个问题不修，报告里的 `semantic Recall@5=0.975 / MRR=0.919` 无法作为选型依据。

另外，报告把 `index_seconds` 解释为「索引耗时」，但计时窗口只覆盖 Embedding + 写库，不覆盖 Semantic 在切分阶段额外发出的 Embedding 请求；fixed 与 semantic 的耗时对比因此并不公平（见 §4.2）。

---

## 2. 阻断性问题

无。

---

## 3. 高优先级发现

### 3.1 Semantic Chunking 严重过切，报告中的 Chunk 数与 manifest 不一致

- 位置：`app/retrieval/chunking.py:209-233`（`SemanticChunker.split`）、`scripts/chunking_experiment.py:162-220`（`_document_chunks`）
- 现象：按 `_strategy_configs()` 的实际配置（`semantic: max_chars=800, threshold=0.55, window=2`）在 `eval/fixtures/phase6_simulated.json` 上复算：
  - `SHOP-DOC-001`（655 字）：semantic 生成 **20** 个 Chunk（长度 18–64）
  - `SHOP-DOC-002`（602 字）：**20** 个
  - `SHOP-DOC-003`（463 字）：**16** 个
  - 文档 Chunk 合计 **56**，加实体 22 → **78** 个；而 `docs/CHUNKING_EXPERIMENT.md` §4.1 与 `results/chunking-2026-10-20.json` 记录的是 **43** 个、平均 119.1 字、最大 196 字。
- 根因：`window=2` 时，窗口序列是逐步右移的（`body[i:i+2]`、`body[i+1:i+3]`……），相邻窗口共享一半文本，**cosine 相似度必然偏高但不是「语义相邻」**；与此同时每遇到一个低相似度点就 `boundaries.add(index + 1)`，在真实 bge-m3 上很容易对每对相邻句子都判低相似度。手工验证：`# A` + 10 段同主题正文，在 `threshold=0.55` 下被切成 **10 个 Chunk**，每个只有一句话。报告记录的最大 196 字、平均 119 字与「严重过切」的直觉并不一致，说明 **manifest 不是当前代码在出报告时真正产出的结果**（或报告后代码又被改动但未重跑）。
- 影响：
  - `semantic` 的 Recall/MRR 来自一组与当前实现不符的 Chunk；
  - 报告 §5 的「semantic 的 MRR 明显最好，值得继续验证」无法成立；
  - manifest 无法复现，实验结论不可审计。
- 建议：
  1. 固定 `window=1`（相邻句子直接比较）或改成「不重叠窗口 + 边界对齐」，避免共享文本造成相似度虚高；
  2. 给 semantic 加最小 Chunk 长度 / 最小句数约束（例如低于 40 字且可合并时并入下一组），否则「每句一 Chunk」会摧毁上下文；
  3. 重新跑真实实验并更新 manifest / 文档，确保 `chunks`、`average_chars`、`max_chars` 与代码一致；
  4. 补一条数据集层面的断言：对 `_strategy_configs()` 的 semantic，文档 Chunk 数不超过某个合理上界（例如 `ceil(total_chars/min_chunk_chars)`），让「过切」在测试里红灯，而不是靠人眼看报告。

---

## 4. 中优先级发现

### 4.1 `index_seconds` 不包含 Semantic 切分期间的 Embedding 调用，策略耗时不可横向比较

- 位置：`scripts/chunking_experiment.py:256-264`
- 计时从 `_index(...)` 开始，而 `items = _all_chunks(...)`（上一行）已经在 `SemanticChunker.split()` 里调用了 `embedding_client.embed_texts(windows)`。手工统计（Fake client）：semantic 在切分阶段额外发 **21 次** `embed_texts`、共 **56** 条文本；fixed / entity 为 0。
- 影响：报告 §4.1 的「semantic 索引耗时 2.5s，与 fixed-256 持平」并不成立——semantic 的真实代价被计到了计时窗口之外；如果按端到端成本看，semantic 明显更贵。这会直接误导「选 semantic 还是 fixed」的判断。
- 建议：把 `_all_chunks()` 纳入计时（或拆成 `chunking_seconds` / `embedding_seconds` / `index_seconds` 三列），报告里明确区分；同时记录切分阶段 Embedding 的调用次数与文本条数。

### 4.2 document 的 `start/end` 偏移与 `content` 不一致，引用追溯会错位

- 位置：`scripts/chunking_experiment.py:173-179`、`app/retrieval/chunking.py:131-141`
- 两类不一致：
  - **entity 文档策略**：`content = f"{path}\n{section.content}"`，但 `start/end` 只指向 `section.content`，Chunk 正文比 span 多出标题路径；
  - **fixed 带 overlap**：`content` 前面拼接了上一块尾部，但 `start` 仍指向当前块主体起点，`chunk.content` 不是 `document.content[start:end]`。
- 手工验证：fixed-256 的 8 个文档 Chunk 全部无法在原文中按 `start:end` 还原（`content not in document.content` 为 8/8）；semantic 有 21/56 个带换行规范化差异；entity 24/24 因前缀而不等。
- 影响：Phase 5 的 Citation 只依赖 `source_id`，暂时不炸；但 Phase 17 完整 Citation Pipeline 若用 `start/end` 做原文高亮 / 精确定位，会指到错误位置。报告里写「便于引用与调试」目前不成立。
- 建议：把 `span_start/span_end` 定义为「Chunk 主体在原文中的范围」，overlap / 标题前缀单独放在 metadata（如 `overlap_prefix`、`heading_path`）；或者让 `start` 指向 `content` 实际起点并在 metadata 里记录 `body_start/body_end`。

### 4.3 Semantic 先把全文按「独立句子」拆开，再用固定窗口拼接，段落边界被抹掉

- 位置：`app/retrieval/chunking.py:38-66`（`_split_units`）、`:216-219`
- `_split_units` 对段落先按空行切，再按句号切；但生成窗口时用 `" ".join(...)` 把多个句子拼成一个字符串。这样：
  - 原本的段落边界不会参与相似度判断（语义边界只由单句向量近似）；
  - 标题行（`# ...`）与正文之间没有显式权重，标题只是碰巧作为 section 的第一个 unit；
  - 对表格 / 列表 / 代码块这类「一行一个单元」的内容，会按行生成窗口，语义切分极易过度。
- 报告 §7 已承认「未覆盖表格 / PDF 版面」，但 Markdown 列表在当前 Phase 5 已经支持，建议在 `CHUNKING_EXPERIMENT.md` 的已知局限里明确写「semantic 对列表 / 代码块不友好」，或让 `_split_units` 把连续的列表项 / 代码块视为一个不可分单元。

### 4.4 manifest 缺少「可复现所需」的运行信息

- 位置：`scripts/chunking_experiment.py:_manifest`
- 当前记录：数据库名、embedding model / dim、策略配置、结果，但**没有 git commit、没有 OMLX 服务版本、没有实际执行命令、没有 random seed**（目前无随机，但 semantic 阈值对模型版本敏感）。
- 影响：报告 §6 的复现命令只有 `--database-url`，无法回答「这份 manifest 是用哪个 commit / 哪个模型版本跑出来的」。在模型或代码被改动后，结果无法审计。
- 建议：manifest 增加 `git_commit`、`embedding_base_url`（脱敏）、`started_at` / `finished_at`、`command`（`sys.argv`），并把 SHA-256 校验从「只对 JSON 做规范化」扩展为「结论表引用该 SHA」。

### 4.5 `entity` 策略的 TestCase / Defect / Requirement 与文档 Section 的「实体级」口径不对等

- 位置：`scripts/chunking_experiment.py:170-180`
- 结构化实体一实体一 Chunk；文档却是一标题 Section 一 Chunk。报告把两者都叫 `entity`，但文档的「实体」其实是标题节点，粒度取决于 Markdown 层级深度。`SHOP-DOC-001` 有 9 个 Section、`SHOP-DOC-002` 8 个、`SHOP-DOC-003` 7 个，而结构化实体合计 22 个，文档 24 个——数值接近是语料构造的巧合，不是口径一致。
- 建议：把策略命名为 `entity+heading`，或在报告中明确「entity 对文档退化为 heading-section」，避免 Phase 7 换 Embedding 时把两个变量混在一起。

---

## 5. 低优先级 / 一致性观察

- **L1 README 的未完成清单自相矛盾**：`README.md` 中 Phase 6 已勾选完成，但同列表仍有 `[ ] 人工 Gold Set 上的 Recall@K / MRR 语义质量评测`。Phase 6 做的正是 Recall@K / MRR，建议改成「扩大到约 100 条人工 Query 的复验」，避免读者以为 Phase 6 没做指标。
- **L2 `docs/CHUNKING_EXPERIMENT.md` 的来源声明需要核对**：§2.1 说「结构参考公开 QA 示例库」，但仓库里的语料是模拟数据；当前写法没问题，建议同时写明「未使用其正文」，这一点正文里已有，但可在 `sources[].usage` 里补一句「仅参考字段组织」。
- **L3 `test_chunking_experiment.py` 用空 Query 集合验证 SQLite 编排**：`dataset.model_copy(update={"queries": []})` 让 `retrieval` 路径完全没跑；`test_run_experiment` 因此无法发现「检索结果与指标累积」类回归。建议在 SQLite 上至少跑 `RetrievalEvaluation` 的纯逻辑测试（已有），并补一个「策略 → items → 指标」的纯内存流水线测试。
- **L4 `fixed-1200` 的实验没有信息量**：3 篇文档最长 655 字，1200 永远不触发。报告已诚实地写了「不能当作大窗口更好的证据」，但策略表仍占用一行；建议在表里加「未触发」标注，或把 fixed-1200 从 MVP 策略集中移除，减少读者误读。
- **L5 `test_timeout_returns_before_worker_finishes` 会留下 2s 的守护线程**：`finished.wait(5)` 让测试等待后台线程跑完，单条测试约 2s；全量仍是 4.8s，可接受，但如果后续加更多慢线程用例，会拖慢 CI。可考虑把慢调用缩到 0.3s，同时把 `rag_request_timeout` 设为 0.05。
- **L6 `semantic` 的阈值 0.55 硬编码在脚本里**，没有经 `Settings` 暴露；报告 §7 已写「换模型必须重校」，但实验脚本无法通过 CLI 覆盖阈值，复验其他阈值需要改代码。建议加 `--semantic-threshold` / `--semantic-window`。
- **L7 `README.md` / `docs/handover.md` 的测试数分别写 339 / 344，与本次实测一致**；但 `results/chunking-2026-10-20.json` 的 `generated_at` 是 `2026-10-19T19:39:15Z`（即本地 03:39），与提交时间 03:41 只差 2 分钟，说明报告是在提交前刚跑完；这本身没问题，但建议在 manifest 里记录 `git_commit` 以便未来审计（见 §4.4）。

---

## 6. 上次 review 问题闭环情况

| 上次问题 | 状态 | 依据 |
|---|---|---|
| 4.1 `backfill_embeddings` 一次性全量 + 单 commit | 已修复 | `_embed_in_batches` 主键游标分页、按 `EMBEDDING_BATCH_SIZE` 逐批 commit；失败批次回滚、已成功批次保留；`tests/services/test_knowledge_chunk_service.py` 新增分批与中途失败用例 |
| 4.2 模块级线程池无 shutdown + 后台任务用请求级 Session | 已修复 | `app/api/rag.py` 改为「闸门 + 守护线程」，`_run_answer` 内 `with session_factory() as session`；`test_worker_uses_its_own_session_not_the_request_session` 断言后台 session 不是请求 session |
| 4.3 满载与超时共用 503 | 已修复 | 新增 `RAGOverloadedError`（429 / `rag_overloaded` / `headers={"Retry-After": "1"}`）；`DomainError.headers` 贯通统一错误信封；测试断言状态码、code、detail、Retry-After |
| 4.4 document 只能补空缺、不能重建 | 已修复 | 新增 `reembed_chunks()` 与 `--rebuild`；`_count_documents(rebuild=...)` 区分 dry-run 口径；测试覆盖「默认只数空缺 / --rebuild 数全部」 |
| L2 空文档清空旧索引 | 已修复 | `ImportService._save_document` 在 `sections=[]` 时跳过删除与元数据更新，保留旧 Document/Chunk；`test_empty_document_does_not_wipe_existing_chunks` + CLI `test_markdown_output_warns_when_nothing_parsed` |
| L4 旧超时用例重复 | 已修复 | 旧的 `test_rag_timeout_returns_llm_error` 已删除，只保留带耗时断言的用例 |
| L5 CHANGELOG 小节标题不准 | 已修复 | 新增小节标题为「Review 修复：Phase 3–5」，内容与范围一致 |
| L6 CLI 提示分支缺测试 | 已修复 | `tests/test_ingest_script.py` 覆盖 table document 提示、markdown 替换向量提示、空文档提示 |
| M2 `/models` 探测 warning | 已修复（上一轮已做） | 本次未回退；README 已知取舍保留 |
| M1/M3/M4（前一轮） | 已修复 | 见 `review/review-phase-3-llm-20261020-0242.md` §6，本次未回退 |

---

## 7. 纪律符合性（本次增量）

| 纪律 | 结论 | 依据 |
|---|---|---|
| TDD 先红后绿 | 符合 | Phase 6 新增 `test_chunking.py`（148 行）、`test_evaluation_metrics.py`（204 行）、`test_chunking_experiment.py`（66 行）；上一轮修复每条都有可失败断言 |
| 测试不依赖 Docker / 外部 API | 符合 | 339 passed 全在 SQLite 内存库；Semantic 测试用 `KeywordEmbedding` 替身；真实实验只在手工执行 |
| PG 专属能力有实机验证 | 符合 | PG 344 passed；真实实验使用 `aitkp_chunking` + pgvector，脚本会检查扩展 |
| Evidence First | 部分 | Gold Set 不绑定 Chunk ID、不可回答 Query 单独统计 distance、报告拒绝用小样本伪造阈值，这些都对；但 §3.1 的 manifest 不可复现，属于「证据链断点」 |
| LLM / Embedding 可替换 | 符合 | `DocumentChunker` Protocol、`EmbeddingClient` Protocol；Semantic 通过注入 embedding_client，无硬编码厂商 |
| 不用 LangChain / LlamaIndex | 符合 | 无新依赖，所有切分 / 指标手写 |
| 统一错误信封 | 符合 | 429 `rag_overloaded` 走 `DomainError` + `headers`；超时仍 503 `llm_error` |
| 约定式提交 + CHANGELOG | 符合 | `fix(review):` / `feat(chunking):`；CHANGELOG 有 Phase 6 条目与 Review 修复小节 |
| 实验隔离 | 符合 | 库名必须含 `chunking`；默认 `--dry-run`；真实实验检查 PostgreSQL + pgvector；脚本会清空目标库并在 docstring 中警告 |
| 欠账显式记录 | 符合 | 报告 §7 明确列出小语料、无英文/表格/PDF、无 Precision/NDCG、阈值依赖模型；README 已知取舍补了「Chunking 结论」与「不可回答阈值」两行 |
| 决策原因写进 docstring | 符合 | `SemanticChunker` 解释「为什么按章节内比较」；`_pack_units` 解释 overlap 不改变 span；`_embed_in_batches` 解释分批提交原因 |

---

## 8. 建议的下一步（按优先级）

1. 修 §3.1：先固定 semantic 的窗口 / 最小 Chunk 约束，重新生成 `results/chunking-2026-10-20.json` 与报告表格；加一条「semantic 文档 Chunk 数上界」测试，防止再次漂移。**在修完并重跑之前，不要引用报告里的 semantic 指标做选型。**
2. 修 §4.1：把切分阶段 Embedding 纳入 `index_seconds`（或拆成 chunking / embedding / index 三段），让 fixed 与 semantic 的成本可比。
3. 修 §4.2：统一 `ChunkDraft.start/end` 的语义，overlap / 标题前缀放 metadata，保证 `document.content[start:end]` 能还原正文。
4. 修 §4.4：manifest 增加 `git_commit`、实际命令、`embedding_base_url`、起止时间，补可审计性。
5. 中低优先级：§4.5 策略命名（entity+heading）；L1 README 未完成项；L3 SQLite 流水线测试；L4 fixed-1200 标注「未触发」；L6 semantic 阈值 CLI 化。
