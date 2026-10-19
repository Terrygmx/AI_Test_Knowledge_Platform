# Changelog

本项目所有值得记录的变更都写在这里。格式参考 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)。

## [Unreleased]

### Added

#### Phase 10：Metadata Filter（2026-10-20）

- **过滤实现**：新增 `app/retrieval/filters.py`，支持 `module` / `sub_module` /
  `priority` / `version` / `status` / `severity` / `case_type` / `doc_type`
  标量精确匹配，以及 `tags` / `related_cases` 数组 contains-any；不同字段 AND。
  未知字段、空数组、空白值、非对象输入直接报错，缺失 / `null` / 类型错误不匹配。
- **跨方言 SQL**：SQLite 使用 `json_each` + `json_type`，PostgreSQL 对每个数组值
  构造单元素 JSONB `@>` 后 OR；过滤谓词在 Vector / BM25 的 `LIMIT` 前应用，
  Hybrid 两个通道共用同一条件。
- **Chunk 接缝**：`KnowledgeChunkService.search()` / `lexical_search()` /
  `hybrid_search()` 新增 `metadata_filters`；统一 `/search` 与 RAG 的公开契约不变。
- **独立数据集**：新增 `eval/fixtures/phase10_simulated.json`，22 条 Query /
  47 个 Chunk，每条 Query 都显式标注 `expected_filters`；保留 Phase 8 原数据集
  与历史 manifest 的 24 条口径。
- **实验与结果**：新增 `app/evaluation/metadata_filter_experiment.py` 与
  `scripts/metadata_filter_experiment.py`，比较 `none / pre / post / during`。
  正式 manifest 为 `results/metadata-filter-phase10.json`，报告见
  `docs/METADATA_FILTER.md`。`post` 故意不复取候选，用于复现候选饥饿。
- **审计边界**：manifest 记录数据集 / 候选哈希、`git_commit` / `git_dirty`、
  脱敏命令与过滤语义；实验表未建 HNSW，`during` 复用 `pre`，延迟只含候选排序 /
  融合，不冒充 ANN、数据库 P95 或 API P95 结论。
- **Review 收口**：Phase 10 使用独立 fixture，避免改写 Phase 8/9 历史数据集；
  非 Mapping 的过滤输入 fail fast；报告一致性测试直接校验 manifest 指标、样本数
  与 ANN 边界。

#### Phase 9：Hybrid Search（2026-10-20）

- **融合实现**：新增 `app/retrieval/hybrid.py`，支持 Weighted 与 RRF；候选以
  `(source_type, source_id, chunk_index)` 去重，保留两个通道的名次、原始分数与
  BM25 `matched_terms`。支持单通道为空和稳定 tie-break。
- **Chunk 检索接缝**：新增 `KnowledgeChunkService.hybrid_search()`，在
  PostgreSQL + pgvector 上组合 Vector 与 BM25，并读取既有 `retrieval_*` 配置。
  统一 `/search`、RAG 与 HTTP 响应契约保持不变。
- **实验与结果**：新增 `app/evaluation/hybrid_experiment.py`、
  `scripts/hybrid_experiment.py` 与 `scripts/hybrid_sweep.py`，固定 Phase 8 的
  24 条 Query / 47 个 Chunk。Vector / BM25 / Weighted / RRF 的 Recall@5 分别为
  0.917 / 0.944 / 0.972 / 0.972，MRR 分别为 0.748 / 0.792 / 0.764 / 0.794。
  原始 manifest 为 `results/hybrid-phase9.json`，敏感性结果见
  `results/hybrid-sweep-phase9.json`，报告见 `docs/HYBRID_SEARCH_EXPERIMENT.md`。
- **可追溯性**：manifest 记录数据集 / 候选 Chunk 哈希、`git_commit` /
  `git_dirty`、脱敏命令、Embedding batch / timeout / retries、模型维度与
  `/models` 元数据。报告一致性测试防止总体、负样本与参数敏感性数字漂移。
- **身份修复**：线上离线 Chunk 评测保留真实 `chunk_index`，避免同来源多 Chunk
  被融合器错误合并；实验的 Vector 与 BM25 通道使用完全相同的 47 个候选。
- **Review 收口**：`--fusion` 现在真正控制评测范围（默认 `all` 比较四通道，
  `weighted` / `rrf` 只评测所选策略），manifest 分别记录 `selection` /
  `evaluated_variants` 与 `default_fusion`；Weighted 遵循
  `higher_is_better` 并拒绝方向混用 / NaN / Infinity；生产向量 SQL 增加身份
  tie-break；两个通道分别使用自己的候选深度；实验 Recall K 复用 Phase 8 的
  动态规则；报告一致性测试逐行校验 RRF 权重扫描指标。
- **边界**：6 条不可回答 Query 在四个通道上仍全部误召回；实验表未建 HNSW，
  延迟只含候选检索与融合，不含 Query Embedding，不能作为 ANN / API P95 结论。

#### Phase 8：BM25 / Lexical Search（2026-10-20）

- **词法检索**：新增 `app/retrieval/lexical.py`，实现应用层分词、倒排索引与
  Okapi BM25（`k1=1.2`、`b=0.75`）。Unicode 字母数字标识符 / 版本号 / 优先级
  整串保留，中文采用 unigram + bigram；不依赖 PostgreSQL 扩展，SQLite 与
  PostgreSQL 共用同一套排序逻辑。
- **Chunk 检索接口**：新增 `KnowledgeChunkService.lexical_search()`，返回
  `(chunk, bm25_score, matched_terms)`；当前只在有明确 BM25 依据的 Chunk 接缝
  暴露分数，统一 `/search` 响应契约保持不变。
- **独立评测集**：新增 `eval/fixtures/phase8_simulated.json`，包含精确业务编号、
  版本、优先级、组合关键词、语义改写与不可回答 Query，共 24 条。
- **实验与结果**：新增 `app/evaluation/lexical_experiment.py` 与
  `scripts/lexical_experiment.py`，离线比较 `ILIKE` 与 BM25。Recall@5 分别为
  0.056 / 0.944，MRR 0.056 / 0.792；`P0 BUG-1234 login-api v1.3` 在 `ILIKE`
  下 0 命中，BM25 首位命中 `BUG-1234`。原始 manifest 为
  `results/lexical-phase8.json`，报告见 `docs/LEXICAL_SEARCH_EXPERIMENT.md`。
- **回归护栏**：新增报告与 manifest 一致性测试，防止指标漂移；PostgreSQL 集成
  路径验证 `lexical_search` 与 SQLite 行为一致。
- **Review 修复**：Recall K 改为由 `top_k` 动态生成（manifest 记录 `top_k` /
  `k_values`），阻止 `--top-k 1` 冒用 Recall@5/10；报告一致性测试覆盖
  `exact_priority`；分词支持 `café` 等 Unicode 拉丁词项；增加中文命中词项、
  服务层输入边界与 1,000 Chunk 规模护栏。Phase 8 数据集明确为 Phase 6 的
  5 条 Defect 加新增 `BUG-1234`（共 6 条）。

#### Phase 7：Embedding 模型对比（2026-10-20）

- **隔离实验**：新增 `app/evaluation/embedding_experiment.py` 与
  `scripts/embedding_experiment.py`，固定 Phase 6 的 `entity+heading` 基线，
  只替换 Embedding；只允许写入库名包含 `embedding` 的 PostgreSQL，并为每个
  模型建立按其真实维度定义的独立 pgvector 表。
- **真实结果**：在 `aitkp_embedding` + 本机 OMLX 上比较 `bge-m3-mlx-4bit`
  （1024 维）、`embeddinggemma-2-oQ8e`（768 维）、`all-MiniLM-L6-v2`
  （384 维）。Recall@5 分别为 0.966 / 0.941 / 0.466，MRR 分别为
  0.936 / 0.934 / 0.447；`bge-m3` 保持默认。
- **可复现性**：manifest 记录数据集 SHA-256、Chunk 统计、模型维度、服务地址、
  起止时间、`git_commit` / `git_dirty`、脱敏命令与分段延迟；原始结果见
  `results/embedding-phase7.json`，报告见 `docs/EMBEDDING_EXPERIMENT.md`。
- **边界校验**：实验客户端可显式关闭“非 1024 维会写生产表”的警告，生产客户端
  仍保留原保护；本实验未建 HNSW，不把 46 行上的精确扫描延迟当作 ANN 结论。
- **重复执行**：每次实验前清理隔离库中的基线实体与 Chunk，修复第二次运行触发
  业务编号唯一键冲突的问题；新增同库连续构建两次的回归测试。

#### Phase 6：Chunking 对比实验（2026-10-20）

- **数据集契约**：新增 `eval/fixtures/phase6_simulated.json`，包含 12 条 TestCase、
  5 条 Defect、5 条 Requirement、3 篇 Markdown 文档和 40 条 Query（34 可回答 /
  6 不可回答）；Gold Set 以来源与原文证据标注，不绑定 Chunk ID。
- **切分实现**：新增 `FixedChunker` 与 `SemanticChunker`；固定策略支持句子 / 段落
  边界和 overlap 64，Semantic 在 Markdown 章节内部按相邻句子低相似度切分，
  `min_chars=40` 合并碎片组，保留标题作为章节锚点，不产生单独标题 Chunk。
- **评测指标**：新增 Recall@5 / Recall@10、MRR、不可回答误召回率、top1 distance
  统计与数据集 SHA-256 规范化。
- **实验编排**：新增 `scripts/chunking_experiment.py`，支持 6 种策略、隔离库保护、
  pgvector 检查、JSON manifest 输出；默认 `--dry-run`，只允许写入名称包含
  `chunking` 的数据库。
- **真实结果**：在 `aitkp_chunking` + OMLX `bge-m3-mlx-4bit` 上完成实验；
  semantic Recall@5 0.975 / MRR 0.919，entity+heading Recall@5 0.966 / MRR 0.936；
  详见 `docs/CHUNKING_EXPERIMENT.md`。
- **边界记录**：固定 1200 在 3 篇短文档上与 fixed-800 完全相同；不可回答 Query
  全部在 Top-10 有结果，说明“检索非空不等于证据充分”，未据此伪造生产阈值。

#### Review 修复：Phase 6 Chunking

- Semantic 改为直接比较相邻句子，修复重叠窗口造成的过切与不可复现问题；增加
  `min_chars=40`、同章节短组归并和章节边界保护，父标题不再孤立成块。
- 文档 Chunk 的 `span_start` / `span_end` 只指向非重叠正文，overlap 与标题路径
  分别写入 `overlap_prefix` / `heading_path` metadata，避免引用高亮错位。
- 实验 manifest 新增 `started_at` / `finished_at`、`git_commit` / `git_dirty`、
  脱敏命令、Embedding 地址、实际策略配置和切分 / 索引分段耗时；Semantic 的
  切分阶段 Embedding 调用次数与文本条数单独记录。
- 策略 `entity` 更名为 `entity+heading`；Semantic 阈值与最短长度支持 CLI 覆盖。
- 重新运行真实实验：Semantic 43 Chunks 与 manifest 一致，entity+heading MRR
  0.936，Semantic MRR 0.919；数据集来源声明明确“仅参考字段组织，未使用正文”。

#### Phase 0：项目边界（2026-10-19）

- 新增 `docs/PROJECT_SCOPE.md`：明确问题、用户、数据、MVP 用例、非目标、成功指标与
  Retrieval / Generation 边界。
- 新增 `docs/LEARNING_QUESTIONS.md`：记录 Embedding、Chunk、Vector / BM25、Rerank、TopK、
  Recall@K / MRR、Faithfulness、RAG 幻觉、适用边界与持续优化问题。

#### Phase 2：Embedding + pgvector（2026-10-19）

- **检索单元**：新增 `knowledge_chunks` ORM / Schema / 迁移 `0002`，包含来源类型、
  来源编号、文档编号、Chunk 序号、正文、Metadata、1024 维向量、内容哈希与版本。
- **向量索引**：PostgreSQL 上使用 `vector(1024)` 与 HNSW `vector_cosine_ops`；
  SQLite 默认测试库仍可建表，但不支持距离运算。
- **Embedding 抽象**：新增 `EmbeddingClient` 协议、OpenAI-compatible 客户端、
  确定性 Fake 客户端、批量维度校验与 SHA-256 内容哈希。
- **索引服务**：TestCase / Defect / Requirement / Document 到 Chunk 的映射，
  支持单条与批量 Embedding 入库。
- **向量检索（链路已通，语义质量待验证）**：`KnowledgeChunkService.search()` 在
  PostgreSQL 上按 cosine distance 返回候选和距离，并支持来源类型过滤；
  SQLite 下明确拒绝而不是返回假排序。Fake Embedding 结果不能作为召回质量结论。
- **索引入口**：`scripts/index_entities.py` 支持全量重建 / `--dry-run` / 按来源过滤；
  先删除该来源旧 Chunk 再重建，重复执行安全；Document 正文解析留到 Phase 5。
- **语义探针**：`scripts/embedding_probe.py` 需真实 `EMBEDDING_API_KEY`，不进入默认测试。
- **Chunk API**：补 `GET /api/v1/chunks`、`DELETE /api/v1/chunks/{id}`；重复写入返回 409。
- **注意**：Fake Embedding 只证明链路可运行，语义召回质量尚未验证。

#### Phase 3：手写最小 RAG（2026-10-20）

- **LLM 抽象**：`LLMClient` + OpenAI-compatible 实现，测试用 Fake，不引入 LangChain。
- **Context Assembly**：按检索顺序生成 `[1] [2]` 证据块，限制单 Chunk / 总上下文字符数。
- **Answer + Citation**：`POST /api/v1/rag/answer`；回答引用编号必须来自本次证据；
  `citation_valid` 只表示引用编号合法，不冒充 Faithfulness；无检索结果时返回
  `evidence_sufficient=false` 与“证据不足”，且不构造 / 调用 LLM。
- **可观测性**：Citation 返回 `rank` / `distance`；支持可选 `max_distance` 阈值；
  `/health.metrics.chunks_without_embedding` 暴露未完成索引的 Chunk 数。
- **命令行入口**：`scripts/ask.py` 支持 `--top-k`、`--source-type` 与 `--max-distance`。
- **验证边界**：Phase 3 只做 Vector Search + TopK，Hybrid / Rerank 仍在后续 Phase。
- **Review 修复**：拆分 `citation_valid` / `evidence_sufficient`，不再用格式合法冒充
  Faithfulness；Citation 暴露 `rank` / `distance`；上下文预算首块也受约束；
  引用解析支持 `[1, 2]` / `[1 ]`；LLM 健康探测改为 60s TTL；
  Chunk 支持 `has_embedding` 过滤，`/health` 暴露缺向量计数。
- **本机 Embedding**：默认切换到 OMLX `bge-m3-mlx-4bit`
  （`http://127.0.0.1:28080/v1`，Key `omlx`），完成真实相似度、索引和
  pgvector 检索验证，详见 `docs/EMBEDDING_EXPERIMENT.md`。
- **本机 LLM + Phase 3 真实链路**：默认 LLM 切换到 OMLX
  `Ling-3.0-tiny-oQ6e`（同一地址与 Key），完成 `scripts/ask.py` 与
  `POST /api/v1/rag/answer` 的 PostgreSQL + pgvector 端到端验证；
  修复“部分回答带合法引用、末尾又写证据不足”时引用被整体丢弃的问题。
- **Phase 3 Review 修复**：`/health` 对 Embedding / LLM 均探测 `/models`
  并校验配置模型存在，服务不可达或模型名错误不再误报 `true`；
  `evidence_sufficient` 只表示是否通过 `max_distance`，与引用合法性解耦；
  无引用回答统一降级为“证据不足”，不再原样返回；新增 `RAG_REQUEST_TIMEOUT`
  （默认 30 秒）与实验性慢接口说明；Citation snippet 截断、`retriever` 收敛为
  受控值，CLI 输出 rank / distance；同步 OMLX / Key / Phase 状态文档。

#### Phase 4：测试知识模型收口（2026-10-20）

- **模型契约验收**：确认 TestCase / Defect 字段、受控词表、唯一业务编号、
  Alembic Schema 与索引完整；知识模型边界见 `docs/PHASE4_KNOWLEDGE_MODEL.md`。
- **Metadata 补齐**：TestCase Chunk 保留 `sub_module` / `case_type` / `tags`；
  Defect Chunk 保留 `related_cases`，为 Phase 10 Metadata Filter 提供完整字段。
- **关联用例校验**：`related_cases` 与业务编号使用相同格式规则，非法编号返回 422；
  仍保持弱关联，不要求关联用例已存在。
- **边界保护**：实体转 Chunk 时容忍尚未 flush 的 ORM 实例中数组字段为 `None`。

#### Phase 5：数据接入（2026-10-20）

- **表格导入**：新增 CSV / Excel 读取、表头归一化、实体类型识别、空值清洗、
  数组字段拆分、文件内去重与按业务编号幂等更新；整文件事务保证失败不留半批数据。
- **Markdown**：新增 front matter、标题树与 Section 解析；正文保留原始 Markdown，
  每个 Section 生成一个 Document Chunk，重复导入按文档替换旧 Chunk。
- **PDF**：使用 `pypdf` 抽取普通文本层，无文本 PDF 明确报错；OCR / 表格 / 版面还原
  不在当前范围。
- **CLI**：新增 `scripts/ingest.py table|markdown|pdf`。
- **文档**：新增 `docs/PHASE5_INGESTION.md`，说明表头、清洗、幂等与边界。

#### Review 修复：Phase 3 / Phase 5（2026-10-20 02:00）

- **无标题文档不再丢正文**：`MarkdownParser` 在无标题时整篇回退为单个 Section，
  首个标题之前的“前言”也保留为 `前言` Section。修复前纯文本 PDF（无标题）会
  导入“成功”却产出 0 个 Chunk，正文整体丢失且无报错。
- **document Chunk 补向量入口**：`KnowledgeChunkService.backfill_embeddings()`
  与 `scripts/index_entities.py --only document` 给 `embedding IS NULL` 的 Chunk
  增量补向量（不重建正文）；`--only testcase,defect,requirement` 仍是重建语义。
  修复前 `docs/PHASE5_INGESTION.md` 承诺的入口实际不认 `document`。
- **重复导入不再静默清向量**：`MarkdownImportResult.indexed_chunks_replaced`
  带回被替换的已向量化 Chunk 数，服务层写 warning，CLI 打印补救命令。
- **RAG 超时真正释放请求**：改用模块级共享线程池（原先每请求 `with
  ThreadPoolExecutor`，退出时 `shutdown(wait=True)` 会把工作时间完整算进请求耗时），
  并加 `RAG_MAX_CONCURRENCY` 并发闸门，满载立即 503。
- **超时预算不再颠倒**：`Settings` 强制 `LLM_TIMEOUT <= RAG_REQUEST_TIMEOUT`
  （默认 LLM 30 秒 / 请求 45 秒，按实测 30 秒生成耗时留余量）；
  README / OpenAPI 如实说明 503 只是提前放弃等待，底层调用无法被中断，
  会继续占用线程到 `LLM_TIMEOUT`。
- **CSV 导入 document 不再丢正文**：`documents` 表无 `content` 列，正文改为写入
  `document` Chunk（按 `doc_id` 替换）；`Type` 列在文档语义下归一为 `doc_type`。
  修复前该列被 Pydantic 静默忽略，正文无声丢失。
- **`/models` 探测可排查**：探测失败 / 未命中模型时写 warning 日志，不再静默吞异常；
  文档标注该探测依赖上游实现 `GET /models` 且模型名精确匹配。
- **`--pairs` 参数删除**：`scripts/embedding_probe.py` 只保留内置测试对这一种模式，
  未知参数显式提示被忽略。

#### Review 修复：RAG 并发 / 补向量分批 / 空文档（2026-10-20 03:00）

- **满载与超时语义分离**：并发超过 `RAG_MAX_CONCURRENCY` 返回 429 +
  `rag_overloaded`（带 `Retry-After: 1`），不再与超时共用 503 + `llm_error`。
  修复前调用方要区分两者只能匹配中文文案；满载应当退避重试，而不是当上游故障处理。
- **后台任务自带会话**：RAG 推理改在守护线程里执行，并通过可注入的会话工厂自己
  开 / 关 Session。修复前把请求级 Session 交给后台任务，超时后（真实连接池下）
  会话会被回收复用，属于「现在没炸、换配置就炸」。
- **进程退出不再被推理拖住**：工作线程改为 daemon，删掉模块级线程池。修复前
  非守护线程池会让 `uvicorn --reload` / 退出等待任务跑完（最长 `LLM_TIMEOUT`）。
- **补向量分批提交**：`backfill_embeddings` / `reembed_chunks` 按
  `EMBEDDING_BATCH_SIZE` 用主键游标分批读取、生成并逐批 commit。修复前一次性读入
  全部 Chunk 且一次 commit 覆盖全部，上游在第 N 批失败时已算出的向量一起回滚，
  大批量补向量等于「要么全成功要么全无」。
- **document 显式重算入口**：新增 `KnowledgeChunkService.reembed_chunks()` 与
  `scripts/index_entities.py --only document --rebuild`。切分策略（Phase 6）或
  Embedding 模型变更后，增量补向量不会刷新旧向量，之前只能手工清表重导。
- **空文档不清索引**：解析出 0 个 Section 时保留该 `doc_id` 的旧 Document 元数据
  与 Chunk 并记 warning，CLI 打印提示。修复前导入一个空文件等于静默清空已有索引，
  还会把标题 / version / path 覆盖成空解析结果。
- **测试整理**：移除与 `test_timeout_returns_before_worker_finishes` 重复的旧超时用例；
  补齐「后台任务不用请求级 Session」「批次失败不回滚」「CLI 提示分支」三处测试。

#### Phase 1：FastAPI + PostgreSQL（2026-10-16，TDD：先写失败测试再实现）

- **ORM 实体**（`app/models/`）：`test_cases` / `defects` / `requirements` / `documents` 四张业务实体表，
  索引命名与迁移一致；`TestCase` 等实体是「原始事实来源」，检索单元 `knowledge_chunks` 留给 Phase 2。
- **可移植列类型**（`app/models/types.py`）：`pk_type()` / `TextArray` / `JSONMap` 按方言分流
  （PG → `BIGSERIAL` / `TEXT[]` / `JSONB`，SQLite → `INTEGER` / JSON 文本），
  使整套测试无需 Docker 即可运行。
- **受控词表**（`app/models/enums.py`）：`Priority` / `CaseType` / `Severity` / `DefectStatus` / `RequirementStatus`，
  统一用 `StrEnum`（`str(member)` 即规范值，避免写入 `"Priority.P0"` 这类脏数据）；
  数据库列保持 `TEXT`，避免 PG 原生 enum 的 `ALTER TYPE` 迁移成本。
- **Pydantic Schema**（`app/schemas/`）：入参统一去空白、枚举大小写归一化、标签去重去空；
  `to_orm_dict()` 用 `mode="json"` 把枚举转成裸值；更新入参 `extra="forbid"`（拼错字段名 / 改不可变业务编号直接 422）。
- **服务层**（`app/services/`）：通用 `EntityService` 基类（CRUD + 等值过滤 + 关键词检索 + 分页），
  四个实体服务只声明差异；`UnifiedSearchService` 提供跨实体统一检索。
- **API**（`app/api/`、`app/main.py`）：`GET /health`（含数据库与 AI 能力自检）、
  `/api/v1/{testcases,defects,requirements,documents}` 完整 CRUD、`GET /api/v1/search`；
  公开接口统一用业务编号（Case ID / Bug ID）寻址。
- **统一错误处理**（`app/api/errors.py`）：所有错误都是 `{"error": {code, message, request_id, detail}}` 信封；
  领域异常（`app/core/exceptions.py`）→ 404 / 409，参数校验 → 422，未处理异常 → 500 且不泄露内部细节。
- **请求中间件**（`app/api/middleware.py`）：`X-Request-ID`（可传入，便于串联上层日志）+
  `X-Process-Time-Ms` + 访问日志，为后续 Badcase 分析打底。
- **Alembic**：`alembic.ini` + `alembic/env.py` + 初始迁移 `0001_initial_schema.py`
  （含 `CREATE EXTENSION vector` / `pg_trgm`，仅 PG）。
- **测试**：覆盖模型形状、Schema 规范化、服务 CRUD/过滤/分页、API 全流程、
  错误信封、中间件、迁移漂移与迁移可执行性。
- **PostgreSQL 实机验证**：新增 PG 专属迁移测试，在真实 PostgreSQL 上从 base 升级到
  `head` 并校验 `vector` / `pg_trgm` 扩展；SQLite 默认路径自动跳过该用例。

### Changed

- **Schema 改由 Alembic 单一管理**：删除 `sql/init/01_schema.sql` 与 docker-compose 的初始化脚本挂载。
  原因：DDL 文件与 ORM 双轨维护必然漂移；现在 `app/models/` 是唯一事实来源，
  `alembic/versions/` 是它的版本化投影，`tests/test_migrations.py` 负责盯住两者一致。
- `docker-compose.yml` 不再挂载 `sql/init`；新增 `scripts/db_init.sh`（起容器 + 等 healthy + 跑迁移）。
- `app/core/config.py` 新增 `api_prefix`（默认 `/api/v1`），`.env.example` 同步补充 `API_PREFIX`。
- `README.md` 重写：目录结构、数据库初始化、测试策略（SQLite 默认 / `TEST_DATABASE_URL` 切 PG）、当前状态与已知取舍。

### Removed

- `sql/` 目录（初稿 DDL 内容已并入 Alembic 初始迁移，未丢失）。

### Notes

- **PostgreSQL 17.11 实机验证通过**：`pgvector/pgvector:pg17` 容器运行正常，
  Alembic 升级到 `0001 (head)`，`vector 0.8.7` / `pg_trgm 1.6` 已安装；
  `aitkp_test` 上 231 passed + 1 skipped，SQLite 默认路径为 229 passed + 3 skipped。
- **Docker 环境**：首次启动需拉取 `pgvector/pgvector:pg17` 镜像；
  测试库 `aitkp_test` 可重复使用，但测试会 `drop_all` + `create_all`，不要指向开发库。
- **中文全文检索待定**：当前关键词检索走 `ILIKE`（全表扫描），Phase 8 再定 zhparser / pg_jieba / 应用层分词。
- **检索结果暂无 score 字段**：没有打分依据时不编数字（Evidence First）；融合分数目前只在 Chunk 接缝暴露。
- **`.env` 中 `EMBEDDING_API_KEY` / `RERANK_API_KEY` 为空**：`/health` 的 `capabilities` 会显示 false，
  涉及 Embedding / Rerank 的代码路径暂不可运行；LLM 能力也改为实际探测，而不是恒为 true。
- **`tags` / `related_cases` 已支持查询过滤**：Phase 10 使用 JSONB / `json_each`
  实现跨方言 contains-any，过滤在候选截断前执行。

### 历史（Phase 0 之前）

- 项目目录骨架，对齐方案 §14 的 `app/` 布局（`core` / `api` / `ingestion` / `retrieval` / `rerank` / `generation` / `evaluation` / `models`）。
- 依赖声明 `pyproject.toml`：FastAPI、SQLAlchemy、Alembic、psycopg3、pgvector、openai、pandas、pypdf、pytest、ruff 等。
- 虚拟环境：`uv sync` + Python 3.11（`.python-version`）。
- 配置层 `app/core/config.py`：基于 pydantic-settings，统一从 `.env` 读取应用 / 数据库 / Embedding / Rerank / LLM / 检索参数。
- 数据访问骨架 `app/core/db.py`：SQLAlchemy 2.0 engine、session、`session_scope()`、`ping()`。
- 日志骨架 `app/core/logging.py`：统一格式，为后续 Badcase 阶段日志打底。
- `.env.example`：Embedding / Rerank / LLM 双通道（Ollama ↔ 云 API）配置模板。
- `.gitignore`：排除 `.venv`、`.env`、`data/`、`.workbuddy-ai/` 等。
