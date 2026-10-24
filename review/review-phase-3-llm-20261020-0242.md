# Review：Phase 3–5 Review 修复（超时 / document 补向量 / 无标题文档）

- 分支：`phase-3-llm`
- 评审时间：2026-10-20 02:42（Asia/Shanghai）
- 评审范围：上次 review（`review/review-phase-3-llm-20261020-0140.md`，基线 `aea36af`）之后的新增提交
  - `3dcc7fb` fix(review)：修复超时、document 补向量与无标题文档丢正文
- 变更规模：20 files changed, 775 insertions(+), 84 deletions(-)
- 复验结果：
  - SQLite 默认：`301 passed, 6 skipped`
  - 真实 PostgreSQL + pgvector（`aitkp_test`）：`306 passed, 1 skipped`
  - `uv run ruff check .` 全绿；`uv run alembic check` 无漂移
  - 手工验证：
    - 超时不再阻塞请求：`rag_request_timeout=1` + 5s 慢调用 → 503 在 **1.04s** 返回（修复前 3.04s / 5.0s）
    - 并发闸门：`_slots=BoundedSemaphore(2)` + 3 个并发请求 → 前两个超时 503（0.34s / 0.31s），第三个立即「并发已满」503（0.0s）
    - 无标题文档：`import_markdown(source="纯文本…")` → `section_count=1`、1 个 Chunk（修复前 0 个）
    - document 补向量：`backfill_embeddings(source_types=["document"])` → 2 条缺向量 Chunk 全部补齐
    - 重复导入：已向量化 2 条 → `indexed_chunks_replaced=2`，服务层 warning + CLI 提示
    - CSV 导入 document：`type` 列归一为 `doc_type=spec`、正文落入 Chunk（`meta.source=table`）；缺 `doc_id` 明确报错
- 工作区：无未提交业务改动（仅 `review/` 下未跟踪的历史 review 文件）

---

## 1. 总体结论

这次增量是「照着上一轮 review 逐条修」的修复提交，质量明显高于普通功能提交：三个高优先级问题都改到了根因，而且每个修复都配了红灯测试，不是靠文案糊过去。

- **H1 超时**：不再是「先给 503 再陪着等」——模块级共享线程池 + `BoundedSemaphore` 并发闸门，实测 5s 慢调用在 **1.04s** 返回；并且把 `llm_timeout=30 <= rag_request_timeout=45` 的约束做进了 `Settings` 的 `model_validator`（配置层 fail fast），README / OpenAPI / 实验文档都如实写明「503 只是放弃等待，底层无法中断」。这是本次最扎实的一处。
- **H2 document 补向量**：`KnowledgeChunkService.backfill_embeddings()` + `scripts/index_entities.py --only document`，增量只补 `embedding IS NULL` 的 Chunk，不重建正文；且刻意区分了「document = 补向量」与「其余 = 按实体重建」两种语义（`_REBUILD_TYPES`）。文档的空洞承诺被填平了。
- **H3 无标题文档**：`MarkdownParser` 现在有整篇回退、前言保留、空标题并入上一段、空正文用标题路径兜底四条边界，并且覆盖到了 PDF 路径（纯文本 PDF 是这一 bug 的主战场）。
- M1（重复导入静默清向量）与 M3（CSV `content` 被静默丢弃）也都修了：前者返回 `indexed_chunks_replaced` + warning + CLI 提示，后者把正文显式写入 document Chunk 并加了落库断言。
- 低优先级 L1（`--pairs`）、L4（README 清单）已闭环，M2 的「静默吞异常」已补 warning，并把 `/models` 探测的适用前提写进了「已知取舍」。

值得肯定的一点：这次把「已知取舍」写得很诚实——超时语义、`/models` 探测前提、`rerank` 只表示配置 Key、document 补向量前不可检索，全部进了 README 表格与 handover「新接手者最容易踩的两条」。这比把限制藏起来有价值得多。

剩下的问题都不阻断，但有一个中等项值得在下个阶段前处理：**补向量是一次性全量 `embed_texts`，没有分批**；另外模块级线程池没有关停路径，超时后后台任务仍持有请求级 Session（docstring 已承认，但实测会话仍可用，属于「能跑但靠运气」）。

---

## 2. 阻断性问题

无。

---

## 3. 高优先级发现

无新增高优先级问题。上一轮的三个高优先级发现（H1 超时不生效、H2 document 无补向量入口、H3 无标题文档 0 Chunk）本次全部闭环，且有实测证据与红灯测试，详见 §6。

---

## 4. 中优先级发现

### 4.1 `backfill_embeddings` 一次性全量调用 Embedding，没有分批

- 位置：`app/services/knowledge_chunk_service.py:189-220`
- 实现是「查出所有 `embedding IS NULL` 的 Chunk → 一次 `embed_texts(全部正文)` → 一次 commit」。`embedding_batch_size=32`（`app/core/config.py:45`）的分批只在 `OpenAICompatibleEmbeddingClient.embed_texts` 内部生效，服务层这一侧仍然：
  - 把所有缺向量 Chunk 一次性读进内存；
  - **一次 commit 覆盖全部**：中途 Embedding 上游失败（第 N 批 5xx）时，前面已算出的向量也一起回滚，`_commit` 没有分批提交，等于大批量补向量是「要么全成功要么全无」；
  - 与 `create_many` 的「先完成 Embedding 再删旧索引，避免留下空索引」的保护思路不一致。
- 当前不致命（数据量小、脚本手动跑），但 Phase 6/12 数据量上来后，一次失败要重跑全部。
- 建议：按 `embedding_batch_size` 分批 `embed_texts` + 分批 commit（或至少失败时保留已成功批次），并补一条「上游中途失败时已补批次不回滚」的测试。

### 4.2 模块级线程池没有关停路径，且超时后后台任务仍在使用请求级 Session

- 位置：`app/api/rag.py:25-30`（模块级 `_executor`）、`app/api/rag.py:71-105`
- `_executor` 在模块导入时创建，lifespan（`app/main.py:36-44`）只 `engine.dispose()`，没有 `executor.shutdown()`；同时它是非守护线程池，进程退出前会等所有任务跑完（实测：提交 5s 任务后主线程退出，进程仍存活 5.17s）。对 `uvicorn --reload` 与测试收集阶段可能带来额外的退出等待。
- 更重要的一处语义风险：`_submit` 把 FastAPI 请求级 `session` 交给后台任务。超时路径下依赖已关闭，docstring 写「后台任务的数据库操作可能失败」，但实测（超时返回后再用同一 session 执行 `SELECT 1`）**会话仍可用**——因为测试夹具用 `StaticPool`，而真实部署下会话会被归还连接池并可能被其他请求复用。这属于「现在没炸，但换个连接池配置就可能炸」。
- 建议：① 在 lifespan 里 `await loop.run_in_executor(None, _executor.shutdown, wait=False)` 或注册 `atexit`；② 后台任务改为自带 Session（在线程内 `SessionLocal()`），请求超时后不再触碰请求级会话——这也正好消除 §4.1 之外最大的一个隐患。

### 4.3 并发闸门与线程池容量同源，但 503 语义混用了「超时」与「满载」

- 位置：`app/api/rag.py:25-30`、`app/api/rag.py:86-92`
- `max_workers` 与 `_slots` 都取 `rag_max_concurrency`，因此「线程池满了」和「信号量取不到」理论上是同一时刻（任务已提交但还没跑完时两者一致），实测第三个请求确实拿到「并发已满」。
- 但目前「满载」与「超时」都返回 503 + `llm_error`，只有 message 不同（「RAG 并发已满，请稍后重试」 vs 「RAG 请求超时」）。调用方要区分只能匹配中文文案；满载更适合 429 + `Retry-After`。
- 建议：给满载一个独立错误码（如 `rate_limited` / HTTP 429），并带上 `max_concurrency` 之外的重试提示。

### 4.4 `document` 类型在 `--only` 里是补向量语义，但没有「强制刷新」出口

- 位置：`scripts/index_entities.py:88-127`
- `--only document` 只处理 `embedding IS NULL`。一旦将来正文切分策略调整（Phase 6 Chunking 实验），旧的 document Chunk 无法通过该脚本重建（因为它不是 `_REBUILD_TYPES`），只能手工清表重导。
- 建议：补一个显式的 `--rebuild document`（或 `--force`）开关，并在 `docs/PHASE5_INGESTION.md` §5 说明「document 默认只补向量，不重建正文」。

---

## 5. 低优先级 / 一致性观察

- **L1 `_release_slot` 的 `ValueError` 分支被标记为 `# pragma: no cover`**：`BoundedSemaphore.release()` 超过初始值时才会抛，正常路径不会触发；用 `ExhaustedSemaphore` 覆盖的测试也顺手把 `release` 改成了 no-op，导致这条容错分支既无测试也不可达。可接受，但值得在注释里说明为什么不做覆盖。
- **L2 无标题 + 空文档仍产出 0 Chunk**：`MarkdownParser.parse("")` → `sections=[]`（`app/ingestion/markdown.py:88-98` 只在 `content` 非空时建 section）。行为本身合理（空文档不该有 Chunk），但 `ImportService` 仍会删旧 Chunk 并 `add_all([])`，即「导入一个空文件等于清空该文档已有索引」。建议：空内容时明确报错或跳过删除。
- **L3 空标题文档仍会以 fallback 标题建 Section**：`parse("## \n\n无锚点正文")` → 1 个 Section（title=`未命名文档`）。符合设计（正文优先于锚点），但与「文档标题来自首个非空标题」的直觉略有出入，建议在 `docs/PHASE5_INGESTION.md` §3 的边界清单里补一句。
- **L4 `tests/api/test_rag_api.py:145` 的旧超时用例与新用例重复**：`test_rag_timeout_returns_llm_error`（0.2s sleep / 0.01s timeout）与新增的 `test_timeout_returns_before_worker_finishes` 断言的是同一条路径，前者没有耗时断言，价值已被后者覆盖，可合并。
- **L5 CHANGELOG 的「Review 修复」小节标题写的是「Phase 3–5」**，但内容只覆盖 Phase 3/5 的修复项（Phase 4 无修复项），建议改成「Phase 3 / Phase 5 Review 修复」，避免误导。
- **L6 `README.md` 与 handover 的测试数字已同步到 301 / 306**（与实测一致），但 `tests/test_ingest_script.py` 没有覆盖 `document_chunks` / `indexed_chunks_replaced` 的 CLI 输出分支——新增的表格提示逻辑只在 `tests/test_ingestion.py` 的服务层有断言。

---

## 6. 上次 review 问题闭环情况

| 上次问题 | 状态 | 依据 |
|---|---|---|
| H1 `rag_request_timeout` 不限制响应时间 | 已修复 | `app/api/rag.py:25-30` 模块级共享线程池 + `BoundedSemaphore`；实测 timeout=1 + 5s 慢调用 → 503 在 1.04s 返回（修复前 3.04s）；新增 `TestRAGTimeoutBoundary.test_timeout_returns_before_worker_finishes`（断言 `elapsed < 1.0` 且工作线程后续仍跑完） |
| H1 次生：`llm_timeout`(120) > `rag_request_timeout`(30) | 已修复 | `app/core/config.py:85-100` 的 `model_validator` 强制 `LLM_TIMEOUT <= RAG_REQUEST_TIMEOUT`；默认改为 30 / 45；新增 `TestSettingsTimeouts` 两条用例（含 `pytest.raises(match="RAG_REQUEST_TIMEOUT")`） |
| H2 document Chunk 无向量化入口 | 已修复 | `KnowledgeChunkService.backfill_embeddings()`；`scripts/index_entities.py` 的 `_REBUILD_TYPES` 区分语义、`--only document` 走增量补向量；`docs/PHASE5_INGESTION.md` §5 改写为「补向量（增量，不重建正文）」；新增 `test_document_chunks_can_be_backfilled_with_embeddings` 与 `test_parse_only_accepts_document` |
| H3 无标题文档 / 纯文本 PDF 产出 0 Chunk | 已修复 | `app/ingestion/markdown.py:88-160`：整篇回退、`前言` Section、空标题并入上一段、空正文用标题路径兜底；实测 `section_count=1` 且 1 个 Chunk；新增 `test_document_without_heading_keeps_whole_body`、`test_preamble_before_first_heading_is_kept`、`test_empty_heading_merges_body_into_previous_section`、`test_empty_body_section_uses_heading_path_as_content`、`test_pdf_without_headings_still_creates_chunks` |
| M1 重复导入静默清向量 | 已修复 | `MarkdownImportResult.indexed_chunks_replaced`（`app/ingestion/service.py:32,116-152`）；表格侧 `TableImportResult.indexed_chunks_replaced`；服务层 warning；`scripts/ingest.py:23-37` 打印补救命令；实测已向量化 2 条 → `indexed_chunks_replaced=2` |
| M2 `/models` 探测静默吞异常、`rerank` 口径不一致 | 已修复 | `app/api/health.py:134-160` 三种失败（非 200 / 未命中 / 异常）均写 warning；`rerank` 加注释说明只表示已配置 Key；README「已知取舍」新增两行 |
| M3 CSV 导入 document 丢 `content` | 已修复 | `app/ingestion/table.py:186-238` 的 `_sync_document_chunk` 把正文写入 `document` Chunk；`Type` 列在文档语义下归一为 `doc_type`；实测 `doc_type=spec`、Chunk 正文与 `meta.source=table` 正确；新增 `test_document_rows_keep_body_in_chunk`、`test_document_rows_without_doc_id_are_rejected`、`test_reimporting_document_rows_reports_replaced_vectors` |
| M4 超时参数倒置 + 每请求新建线程池 | 已修复 | 同 H1 |
| L1 `embedding_probe.py --pairs` 空操作 | 已修复 | 参数删除，改为 `parse_known_args` + 未知参数显式提示；README / `docs/EMBEDDING_EXPERIMENT.md` / handover 命令同步 |
| L2 空标题过滤无注释 | 已修复 | `_add_section` 与 `_with_fallback_content` 均有完整 docstring 说明取舍 |
| L3 CLI 未提示 Chunk 无向量 | 已修复 | `scripts/ingest.py:_print_embedding_tips`，table（document）/ markdown / pdf 三条路径均提示 |
| L4 README 未列 Phase 5 CLI | 已修复 | README「当前状态」补 Phase 5 与补向量入口两条；`.env.example` 新增 `RAG_REQUEST_TIMEOUT=45` / `RAG_MAX_CONCURRENCY=4` |
| L5 测试未覆盖无标题 / document 补向量 | 已修复 | 见上（H2 / H3 行） |

---

## 7. 纪律符合性（本次增量）

| 纪律 | 结论 | 依据 |
|---|---|---|
| TDD 先红后绿 | 符合 | 每条修复都配了断言到根因的测试：超时的耗时断言（`elapsed < 1.0` + `not finished.is_set()`）、0 Chunk 的反例断言（含 content_hash 不等于空串 sha256）、CSV 正文落库断言 |
| 测试不依赖 Docker / 外部 API | 符合 | 301 passed 全在 SQLite 内存库；`FakeEmbeddingClient` 用于补向量测试；真实 OMLX 只出现在文档与手工验证 |
| PG 专属能力有实机验证 | 符合 | PG `306 passed, 1 skipped`；`alembic check` 无漂移 |
| Evidence First | 符合 | 补向量前的「不可检索」状态被显式暴露（`chunks_without_embedding`、`indexed_chunks_replaced`、CLI 提示、README/handover 双处标注）；不再有「导入成功但检索不到」的静默失败 |
| LLM 可替换 | 符合 | `LLMClient` Protocol 未变更 |
| 不用 LangChain / LlamaIndex | 符合 | 无新依赖；`markdown.py` / `table.py` 全部手写 |
| 统一错误信封 | 符合 | 超时与满载都走 `LLMError`（503 + `llm_error`）；配置非法在 Settings 层 `ValueError`；表格缺 `doc_id` 为 `ValueError` 并由 CLI 明确退出 |
| 约定式提交 + CHANGELOG | 符合 | `fix(review):` 一条；CHANGELOG 新增「Review 修复：Phase 3–5」小节，逐条列出修复前后对比 |
| 欠账显式记录 | 符合 | README「已知取舍」新增 4 行（文档导入不生成向量 / RAG 超时语义 / `/models` 探测前提 / Rerank 能力位）；handover 新增「Phase 5 的两个使用前提」；`docs/PHASE5_INGESTION.md` 明确写下「早期实现会静默丢正文」这个已踩的坑 |
| 决策原因写进 docstring | 符合 | `_executor` / `_slots` / `_release_slot` / `backfill_embeddings` / `_sync_document_chunk` / `_add_section` / `_with_fallback_content` / `_print_embedding_tips` 均有取舍说明 |

---

## 8. 建议的下一步（按优先级）

1. 修 §4.2：后台 RAG 任务改用自带 Session（`SessionLocal()`），并在 lifespan 关停线程池——这是唯一「现在能跑但换配置就可能炸」的点。
2. 修 §4.1：`backfill_embeddings` 按 `embedding_batch_size` 分批 + 分批提交，避免大批量补向量一次失败全回滚。
3. 修 §4.3：满载返回 429 + 独立错误码，不再与超时共用 503。
4. 修 §4.4：给 document 补一个显式重建开关（Phase 6 Chunking 实验前需要）。
5. 低优先级：L2 空内容导入时报错或跳过删除；L4 合并重复超时用例；L5 CHANGELOG 小节标题改准；L6 补 CLI 提示分支的测试。
