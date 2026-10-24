# RAG 学习指南：从每个 Phase 学到什么、怎么学、怎么确认

- 日期：2026-10-24（Asia/Shanghai）
- 适用读者：想在 RAG 方向建立完整、可验证能力的工程师。
- 使用方法：每个 Phase 都按「学到什么 → 怎么学 → 怎么确认 → 例子」四段展开。
  先读 `docs/PROJECT_SCOPE.md` 建立全局，再按顺序学；每章至少完成"怎么确认"
  里的一个动作再进入下一章。
- 通用环境：`docker compose up -d` 启动 PostgreSQL；所有命令在仓库根目录执行；
  遇到 `--dry-run` 先跑一遍看计划再真跑。

总览（详表见 `docs/PHASE_PROGRESS.md`）：

| 阶段 | 主题 | 一句话收获 |
|---|---|---|
| Phase 0 | 项目边界 | RAG 先定义"为谁解决什么问题、怎么算成功" |
| Phase 1 | API 与数据库 | 工程化基座：Schema 即契约 |
| Phase 2 | Embedding | 文本如何变成可检索的向量 |
| Phase 3 | 最小 RAG | 一条完整的检索→生成链路长什么样 |
| Phase 4 | 知识建模 | 原始事实与检索表示要分离 |
| Phase 5 | 数据接入 | 脏文件进，干净 Chunk 出 |
| Phase 6 | Chunking | 切分方式直接决定检索上限 |
| Phase 7 | Embedding 对比 | 控制变量地选模型 |
| Phase 8 | BM25 | 关键词检索仍是主力，不可跳过 |
| Phase 9 | Hybrid | 向量与词法互补，RRF 是低成本融合 |
| Phase 10 | Metadata Filter | 先过滤后检索，语义与性能都要对 |
| Phase 11 | Reranker | 粗召回 + 精排是性价比路线 |
| Phase 12 | 评测数据集 | Gold Set 是一切指标的地基 |
| Phase 13 | 检索评测 | Recall@K / MRR / NDCG 到底在测什么 |
| Phase 14 | 生成评测 | 忠实度与引用正确性可以量化 |
| Phase 15 | Query Understanding | 把自然语言变成结构化意图 |
| Phase 16 | Query Rewrite | 改写要用指标验证，不是玄学 |
| Phase 17 | Citation | 引用要能追溯到 Chunk 和分数 |
| Phase 18 | 完整 Pipeline | 逐阶段可观测是排障的前提 |
| Phase 19 | Badcase 闭环 | 错误要分类、要闭环、要回归 |
| Phase 20 | 性能统计 | 分位数与分阶段延迟 |
| Phase 21 | 增量索引 | 内容哈希驱动最小变更 |
| Phase 22 | 版本管理 | 知识库也要有版本概念 |
| Phase 23 | 权限 | 过滤在检索层做，不在展示层做 |
| Phase 24 | Dashboard | 指标聚合要带溯源 |
| Phase 25 | Agentic RAG | 多步检索是工具调用，不是自由发挥 |
| Phase 26 | 回归 Agent | 自动化产出要有人工批准门槛 |
| Phase 27 | 实验矩阵 | 缺失数据要显式标记 |
| Phase 28 | 架构文档 | 结论要能追溯到报告 |
| Phase 29–30 | 计划与验收 | 工程完成 ≠ 质量验收 |

---

## Phase 0：项目边界

**学到什么。** RAG 项目失败最常见的原因不是模型不行，而是目标不清：不知道用户是谁、
检索失败是否比生成失败更致命、多少延迟算可用。Phase 0 产出三份文件
（`docs/PROJECT_SCOPE.md`、`docs/LEARNING_QUESTIONS.md`、实现计划），把
MVP 范围、非目标、成功指标和学习问题全部写死。学到的方法论是：**先写"不做什么"**，
因为范围蔓延的代价在 RAG 项目里尤其高——每个新需求都会牵动 Chunking、Embedding、评测三件套。

**怎么学。** 精读 `docs/PROJECT_SCOPE.md` 的"非目标"与"成功指标"两节；对照
`docs/LEARNING_QUESTIONS.md` 里的 10 个问题，每个问题问自己：如果只允许我做一次实验来回答它，
我会怎么设计？再读 `docs/AI_Test_Knowledge_Platform_Implementation_Plan.md`，
观察 Phase 划分如何把 10 个学习问题逐个变成实验。

**怎么确认。** 合上文档回答三个问题：这个项目的用户是谁？MVP 交付什么、明确不交付什么？
Phase 6–14 各自回答哪个学习问题？三个都答得出即通过。

**例子。** "支持 PDF 里的扫描件"出现在非目标里。学习点：OCR 属于数据接入质量
问题（Phase 5），把它放进 MVP 会让所有检索实验混入"解析失败"噪声。学到的是——
实验阶段应保持语料干净，解析质量问题单独治理。

## Phase 1：FastAPI + PostgreSQL

**学到什么。** RAG 系统首先是信息系统：统一的错误信封、请求追踪中间件、Alembic
迁移、四类业务实体 CRUD。核心收获是"Schema 即契约"——Pydantic 模型同时约束
API 入参、数据库写入和文档生成，一处定义三处生效。以及为什么统一错误信封重要：
调用方只需要解析一种错误结构，监控只需要按 `error.code` 聚合。

**怎么学。** 读 `app/main.py`（应用工厂与生命周期）、`app/api/errors.py`
（统一错误信封）、`alembic/versions/0001_initial_schema.py`（迁移即 Schema 投影）。
对照 `tests/api/test_errors.py` 看每个错误码如何被测试锁定。

**怎么确认。** 启动服务后：故意 POST 一条缺字段的数据，确认返回 422 且带
`validation_error` 代码；用 `alembic downgrade base && alembic upgrade head`
完整走一遍迁移；回答"为什么错误信封里不能回显用户输入"（安全边界，Phase 10 也会再遇到）。

**例子。** 提交 `{"title": " "}`（全空白标题）会得到字段级错误信息而不是 500。
学习点：校验发生在 Schema 层，业务代码不用重复写 `if not value`。

## Phase 2：Embedding 与 pgvector

**学到什么。** Embedding 把一段文本变成 1024 维向量，语义相近的文本向量距离更近；
pgvector 的 `cosine_distance` 用 `<=>` 计算"方向差"。三个关键认知：
①向量是**检索表示**不是存储优化，换 Embedding 模型必须全部重算；
②维度由模型决定，写入前必须校验（`_validate_dimension`）；
③SQLite 测试环境没有向量运算，项目用"明确报错"而不是"假装能检索"来守住真实性。

**怎么学。** 读 `app/retrieval/embedding.py` 的 `EmbeddingClient` 协议、
`OpenAICompatibleEmbeddingClient.embed_texts`（分批、重试、维度校验）和
`KnowledgeChunkService.search` 的距离排序 SQL。跑一次探针：
`uv run python scripts/embedding_probe.py`，看真实模型返回的向量维度。

**怎么确认。** 手工算一次 cosine 距离：取两段相似文本（如"登录失败"与"登录报错"）
和一段无关文本（"今天天气"），用探针拿向量后手算（或让脚本算），确认相似对距离更小。
再回答：为什么 `search` 在 SQLite 上抛 `DomainError` 而不是退回 ILIKE？

**例子。** "bge-m3 1024 维"写入配置后，任何 768 维向量入库都会被拒。学习点：
模型、维度、数据库列三方必须锁死，否则静默降维会让检索质量无声劣化。

## Phase 3：手写最小 RAG

**学到什么。** 一条最小 RAG 链路 = Vector Search → Context Assembly → Prompt → LLM →
答案 + 引用编号校验。这一阶段学到的三个铁律贯穿全项目：
①**Evidence First**——LLM 只能引用检索到的证据，引用编号越界即整体拒绝；
②无证据时拒答（`INSUFFICIENT_EVIDENCE`）比编造好；
③同步推理是慢接口，需要超时（503）与并发闸门（429）两个不同的保护。

**怎么学。** 通读 `app/generation/rag.py`（重点 `build_prompt` 与
`extract_citations`）、`app/api/rag.py` 的并发闸门实现（注释里写了从
ThreadPoolExecutor 改成"闸门 + 守护线程"的原因）。用
`uv run python scripts/ask.py "登录失败怎么排查"` 亲眼看 prompt 与答案。

**怎么确认。** ①让 LLM 回答 `[5]`（只有 3 条证据）观察拒绝路径；
②清空知识库再问，确认收到拒答而不是幻觉；③设置一个 1 秒超时，确认 503 在 1 秒内返回
而工作线程仍在跑（看 `tests/api/test_rag_api.py` 的超时测试如何断言"提前返回"）。

**例子。** prompt 模板里明确写了"如果证据不足以回答，请回答：证据不足"。学习点：
拒答是设计出来的行为，需要在 prompt、校验、评测（Phase 14 的 Refusal Correct）三层同时落实。

## Phase 4：测试知识模型

**学到什么。** 领域建模决定检索上限：TestCase / Defect / Requirement / Document 四类实体
各自有业务编号（Case ID / Bug ID / Req ID / Doc ID）、优先级、版本、模块等元数据。
核心设计是**事实来源与检索表示分离**：原始实体是事实，`knowledge_chunks` 只是
可删可重建的检索投影；Citation 永远锚定业务编号而不是 Chunk ID。

**怎么学。** 读 `docs/WHY-STRUCTURED-KNOWLEDGE.md`（为什么结构化知识优于纯文档堆），
再读 `app/models/test_case.py` 与 `app/services/index_service.py` 的 `case_to_chunk`，
观察实体字段如何被拼装成检索正文和 metadata。

**怎么确认。** 用 API 创建一个 TestCase，然后找出它对应的 KnowledgeChunk 行，
对照三个问题：哪些字段进了正文？哪些进了 metadata？为什么 `metadata` 列在
Python 侧叫 `meta`（SQLAlchemy 保留字问题）？

**例子。** `case_to_chunk` 把"用例编号 / 标题 / 前置条件 / 步骤 / 预期结果"拼成正文，
把 `module / version / priority` 放进 metadata。学习点：正文影响召回（要含关键词），
metadata 影响过滤（要结构化），两者的分界就是 Phase 10 的字段白名单来源。

## Phase 5：数据接入

**学到什么。** CSV / Excel / Markdown / PDF 四种解析器 + 幂等导入。两个关键决策：
①导入与 Embedding 解耦——批量导入不调模型，向量由索引脚本统一补，失败可重试；
②**空文档不能清空索引**——导入一个空 Markdown 会静默删光旧 Chunk，让文档"凭空不可检索"，
代码里对此有专门防御并如实报告"未发生替换"。

**怎么学。** 读 `app/ingestion/service.py` 的 `_save_document`（重点看
`indexed_before` 的防御逻辑）和 `docs/PHASE5_INGESTION.md` 的字段映射。
用 `scripts/ingest.py markdown` 导入一个真实文件，再用
`scripts/index_entities.py --only document` 补向量，观察两步的输出差异。

**怎么确认。** ①导入同一文件两次，确认第二次不产生重复 Chunk（幂等）；
②导入空文件，确认提示"没有可导入的正文，已保留原有 Chunk"；
③回答：为什么导入后系统会提示"Chunk 尚未生成向量，还不能参与检索"？

**例子。** 表格导入按表头自动识别实体类型，`case_id` 列映射业务编号。
学习点：接入层的产出是"干净、可追溯、可重放"的结构化数据，任何解析歧义都应报错而不是猜测。

## Phase 6：Chunking 实验

**学到什么。** Chunking 是检索质量的第一杠杆。五种策略（固定 256/512/800/1200、
语义切分、entity+heading）在同一 Gold Set 上对比。核心方法论：**Gold 粒度绑定
来源实体 + 原文证据，不绑定 Chunk ID**——切分策略变化会改变 Chunk 编号，绑 Chunk
的标注在换策略后全部作废。以及"证据匹配"的定义：证据子串出现在 Chunk 正文里才算命中。

**怎么学。** 读 `docs/CHUNKING_EXPERIMENT.md` 的结论与局限，再读
`app/retrieval/chunking.py` 的 `FixedChunker`（句子/段落边界收口）和
`SemanticChunker`（为什么按章节内判断相似度而不是全篇统一判断）。
跑：`uv run python scripts/chunking_experiment.py --dry-run` 看计划，再去掉 dry-run 真跑。

**怎么确认。** 打开 `results/chunking-2026-10-20.json`，找到各策略的 Recall@5；
手工验证一个 case：拿一条 query 的 Gold 证据原文，在 `entity+heading` 的切分结果里
找到包含它的 Chunk。能解释"为什么 256 字符的小 Chunk 在这个语料上表现如何"即通过。

**例子。** 固定 512 切分可能把"步骤 3"和"预期结果"切进不同 Chunk，导致
检索到步骤却丢掉预期结果；entity+heading 以实体为界，天然完整。
学习点：切分不是技术参数，是"证据完整性"的业务决策。

## Phase 7：Embedding 模型对比

**学到什么。** 模型选型的实验纪律：**固定 Chunking，只换 Embedding**，同时记录
Recall、MRR、延迟、维度和成本。学到"隔离变量"这个实验设计基本功——任何两个模型
的对比，若连切分都不一致，结论就是无效的。以及延迟不能只看平均值，
要记录分布（分位数在 Phase 20 正式化）。

**怎么学。** 读 `docs/EMBEDDING_EXPERIMENT.md` 的实验设置与"局限"一节
（46 个 Chunk 的延迟不代表生产 P95），再读 `scripts/embedding_experiment.py`
如何为每个模型建隔离表、如何记录失败样本。

**怎么确认。** 对照 `results/embedding-phase7.json` 回答：三个模型里哪个 Recall 最高？
哪个延迟最低？如果生产环境每天 10 万次查询，怎么结合成本与延迟做选择？
再跑一次 `--dry-run` 确认自己能预测输出结构。

**例子。** 1024 维模型 Recall 略高但比 384 维慢一倍。学习点：没有"最好"的模型，
只有在给定 QPS、预算、精度要求下的最优权衡；实验报告的价值是把权衡摊开，而不是给唯一答案。

## Phase 8：BM25 词法检索

**学到什么。** BM25 是关键词检索的工业标准：词频（TF）、逆文档频率（IDF）、
文档长度归一化（参数 k1、b）。中文要先分词；标识符（如 `SHOP-LOGIN-001`）
必须整串保留，不能被切碎。两个工程要点：①应用层倒排索引在每次查询时内存重建——
小规模可行，数据量一大必须持久化；②BM25 是"包含任一词"的打分，天然补充向量检索的
"语义相近"盲区。

**怎么学。** 读 `app/retrieval/lexical.py` 的 `tokenize` 与 `BM25Index`
（倒排表结构、打分公式），对照 `docs/LEXICAL_SEARCH_EXPERIMENT.md` 的
失败模式实验（标识符、版本号、组合关键词）。
跑：`uv run python scripts/lexical_experiment.py --dry-run`，再调
`--k1 1.2 --b 0.75` 观察参数影响。

**怎么确认。** 用 `SHOP-LOGIN-001` 这样的标识符做 query，确认 BM25 能精确召回而
向量检索可能失败；解释 k1 调大/调小分别如何影响"长文档 vs 短文档"的相对得分。
跑通后打开 `results/lexical-phase8.json` 找到 ILIKE 与 BM25 的差距。

**例子。** query "v2.4 登录" 中，BM25 对同时包含两词的文档给高分，而向量检索可能
被"v2.4"稀释语义。学习点：用户 query 里的硬信息（版本号、编号）是词法检索的主场。

## Phase 9：Hybrid Search

**学到什么。** 两路结果怎么合并：Weighted（归一化加权，对分数尺度敏感）与
RRF（倒数排名融合，`1/(k+rank)`，只看排名不看分值，鲁棒）。核心工程细节：
①两路候选要按 `(source_type, source_id, chunk_index)` 去重；
②tie-break 必须稳定，否则同分结果的顺序抖动会污染评测；
③候选深度（每路取多少）比融合参数更影响召回。

**怎么学。** 读 `app/retrieval/hybrid.py` 的 `fuse`（重点 RRF 的 k 与排名计算、
higher_is_better 方向翻转），对照 `docs/HYBRID_SEARCH_EXPERIMENT.md` 的参数扫描。
跑：`uv run python scripts/hybrid_experiment.py --fusion rrf --dry-run`，
再用 `--fusion weighted` 对比同一数据集的指标差异。

**怎么确认。** 手算一遍 RRF：两条候选分别排第 1 和第 3，k=60，各自贡献多少分？
合并后谁是第一？能手算即真正理解。再从 `results/hybrid-sweep-phase9.json`
找出"候选深度从 10 提到 20 后 Recall 变化多少"。

**例子。** 向量第一名和 BM25 第一名是同一篇文档时，RRF 相加后它稳居榜首；
两路各推一篇不同文档时，RRF 让它们并列靠前而不用管原始分值差异。
学习点：RRF 用"排名的倒数"规避了"BM25 分值 15 和 cosine 距离 0.3 无法直接相加"的问题。

## Phase 10：Metadata Filter

**学到什么。** 受控过滤 DSL：白名单字段（module / priority / version / tags…）、
标量精确匹配、数组"包含任一"。四条铁律：①过滤必须发生在 **LIMIT 之前**（SQL WHERE），
否则前 N 名全被过滤掉会发生"候选饥饿"；②缺失字段、null、错误类型**不匹配**，
不能把"没有元数据"当成满足条件；③未知字段直接报错而不是静默忽略——静默忽略会让用户
以过滤生效实际没生效；④SQLite 与 PostgreSQL 语义必须一致（JSON 类型守卫）。

**怎么学。** 读 `app/retrieval/filters.py`（`normalize_metadata_filters` 的全部拒绝分支
和 `_string_scalar_predicate` 的类型守卫），对照 `tests/test_metadata_filters.py`。
跑：`uv run python scripts/metadata_filter_experiment.py --offline --dry-run`。

**怎么确认。** 构造三个场景并预测结果再验证：`{"module": "login"}` 遇到
无 metadata 的 Chunk 会怎样？`{"tags": []}` 为什么直接报错？数组过滤
`["smoke","security"]` 是"都要有"还是"有其一"？（答案：有其一，代码注释里专门警告了
`@>` 包含全部语义的坑。）

**例子。** `module=payment` 的查询不应返回"没有模块信息的文档"。学习点：
过滤语义宁可严格（漏召回）也不能宽松（错召回），因为过滤通常是用户强意图。

## Phase 11：Reranker

**学到什么。** 粗召回 → 精排的两段式：Hybrid 先取宽候选（如 top 20），
Reranker 用更重的模型对候选与 query 逐一打分，输出最终 top 5。
学到三个工程契约：①同一 run 必须同时输出 baseline 与 rerank 两列指标，才可对比；
②provider 返回少于请求数是合法行为，返回空集不是（必须报错）；
③预检（preflight）要在建库之前做——连接配置错误应在花钱耗时之前暴露。

**怎么学。** 读 `app/rerank/service.py`（`/rerank` 请求结构、重试、预检）与
`app/rerank/hybrid.py` 的 `rerank_fused_hits`（候选深度 → top_n 的衔接）。
跑：`uv run python scripts/rerank_experiment.py --dry-run`，观察计划里的双通道设计。

**怎么确认。** 回答三题：为什么 rerank 之后不重新检索（精排只重排候选）？
provider 返回 3 条（要了 5 条）时代码怎么处理？返回 0 条呢？
再对照 review 文件 `review/` 里 Phase 11 的记录，理解"默认模型 409 不可用"这个
现实约束如何被如实记录而不是掩盖。

**例子。** 混合检索把正确文档排到第 4（RRF 分数接近），Reranker 把它提到第 1。
学习点：召回阶段宁可宽（排名不准没关系），精排阶段才追求准——这就是
`rerank_candidates=20 → top_n=5` 的参数含义。

## Phase 12：Evaluation Dataset

**学到什么。** Gold Set 的构造学：六类固定配额（用例/缺陷/需求/文档/跨版本/不可回答）
各司其职；每条数据必须包含 query、期望意图、过滤条件、Gold 来源、**Chunk 级证据**
（证据子串必须真实出现在语料中）、答案要点；不可回答的 query **不标 Gold**——它们考的是拒答。
最重要的一条纪律：AI 只能生成候选，`confirmed` 状态必须由人（Terry）逐条给出，
带时间戳和理由，代码层面锁死。

**怎么学。** 读 `docs/EVALUATION_DATASET.md` 的 Schema 与审阅流程，再读
`app/evaluation/phase12_dataset.py` 的校验器（`_validate_answerability`、
`_validate_review_provenance`——注意 pending 禁带审阅元数据的反向校验）。
体验审阅工具：`uv run python scripts/phase12_review.py progress`。

**怎么确认。** ①导出 checklist 并人工审阅前 5 条：对照语料原文核对证据子串是否真实存在、
意图与分类是否一致；②故意把一条改成 confirmed 但不写 notes，确认被拒；
③回答：为什么 unanswerable 类别禁止标注 gold_documents？

**例子。** "跨版本"类 query 必须带 `expected_filters` 且 Gold 文档覆盖两个版本。
学习点：类别配额逼你覆盖所有检索场景，"全是简单事实查询"的 Gold Set 会漏测过滤与拒答。

## Phase 13：Retrieval Evaluation

**学到什么。** 四个指标的真实含义：Recall@K（前 K 名里命中了几条 Gold，看"找没找到"）、
Precision@K（命中占比，看"噪声多不多"）、MRR（第一条命中的排名倒数，看"多快找到"）、
NDCG（排名越靠前得分越高，且做归一化）。学到三个实现级坑：
①重复计分——同一 Gold 被多个结果命中时只能计一次，否则指标可以超过 1；
②结果哈希要稳定（同输入同哈希）才能做回归对比；
③candidate / formal 双模式：formal 强制要求人工确认过的数据集。

**怎么学。** 读 `app/evaluation/retrieval_evaluation.py` 与 `app/evaluation/metrics.py`
（重点看 NDCG 的 DCG/IDCG 计算和去重逻辑），对照 `tests/` 里的指标单测
（每个坑都有对应测试）。跑：
`uv run python scripts/retrieval_evaluation.py --phase12-dataset eval/fixtures/phase12_candidates.json --dry-run`。

**怎么确认。** 手算一个例子：Gold 有 2 条，检索结果 [A, B, C] 中 A、B 都是 Gold——
Recall@3、Precision@3、MRR、NDCG@3 各是多少？然后打开
`results/retrieval-phase13.json` 对照每条 query 的逐项指标与 badcase 分类。

**例子。** 一条 query 的 Gold 在第 8 名才出现：Recall@10 = 1 但 MRR = 0.125。
学习点：单看 Recall 会高估质量，MRR 暴露"用户要翻很多条"的真实体验；两个指标必须一起看。

## Phase 14：Generation Evaluation

**学到什么。** 生成质量怎么量化：Faithfulness（每个句子是否有上下文支持）、
Citation Accuracy（答案里的 `[n]` 编号是否指向真实证据）、Answer Relevancy
（是否回答了问题，启发式）、Context Recall/Precision（证据是否覆盖答案要点、
是否混入无关上下文）、Refusal Correct（该拒答的是否拒答）。学到归因分离：
**检索错误 vs 生成错误**必须分开报告——检索没找到证据却回答了，与检索对了答案写错，
是完全不同的修复路径。以及 candidate 结果不能冒充 formal 结论。

**怎么学。** 读 `docs/GENERATION_EVALUATION.md` 的指标定义，再读
`app/evaluation/generation_evaluation.py`（重点：拒答句豁免、混合回答不豁免、
gold_evidence 同时校验 Chunk 身份和正文）。跑：
`uv run python scripts/generation_evaluation.py --dry-run`。

**怎么确认。** 构造四个回答并预测指标：①全部来自上下文且引用正确；②含一句
编造内容；③引用 `[9]` 越界；④证据不足却正常回答。跑评测验证预测。
再打开 `results/generation-phase14.json` 看逐样本 badcase 的归因标签。

**例子。** 答案写了"超时后使用原幂等键查询交易状态"但上下文只有"使用原幂等键"，
"超时后"三个字就是 unsupported claim。学习点：Faithfulness 是句子级的，
一个漂亮的答案可以混入一个致命的臆断。

## Phase 15：Query Understanding

**学到什么。** 把"v2.4 的登录缺陷"解析成 `{intent: defect_search, module: "login"?,
version: "v2.4", filters: {...}}`。两条设计原则：①LLM 输出必须过 Pydantic 严格校验
（未知字段拒绝、枚举校验），自由文本不能直接进检索；②确定性规则是默认基线，
LLM 是可替换增强，失败时降级并记录 `accepted_by`（谁做的决定可追溯）。
以及 `semantic_query` 保留原问题的原因：中文 bigram 切碎会破坏向量召回。

**怎么学。** 读 `app/query/understanding.py`（`_INTENT_TERMS` 关键词表、
`LLMQueryUnderstanding` 的 JSON 解析与重试、fallback 链路）。
跑：`uv run python scripts/query_understanding_experiment.py --dry-run`，
然后真跑对比 deterministic 与 LLM 的解析结果。

**怎么确认。** 手写 5 条 query 预测解析结果再验证："P0 的支付缺陷"（意图/优先级/模块）、
"v2.3 到 v2.4 的变化"（版本）、"SHOP-LOGIN-001 的预期结果"（业务编号）。
再回答：为什么 LLM 返回两个 JSON 代码块要整体拒绝而不是取第一个？

**例子。** "退款"映射到 `module=refund` 而不是关键词堆砌。学习点：
解析器输出的 filters 直接进 Phase 10 的白名单管道，任何越界字段都会被拒——
理解模块之间如何咬合，比记住单个模块更重要。

## Phase 16：Query Rewrite

**学到什么。** 改写策略链：NoOp（不改写，永远要有）、Deterministic（规则）、LLM、
Composite（组合 + 失败降级）。核心教训反而来自"没有收益"：deterministic 改写在候选集上
**一条都没改**（`changed_queries=0`），所以本阶段只能下"工具链可用"的结论，
不能下"改写有效/无效"的结论。学到：**改写必须有指标对照，且 noop 是必备对照组**。

**怎么学。** 读 `app/query/rewrite.py`（`RewriteResult` 的 fallback_reason、
Composite 的串联逻辑）与 `docs/QUERY_UNDERSTANDING_REWRITE.md` 的结果解释。
跑：`uv run python scripts/query_rewrite_experiment.py --dry-run`，
对比 `results/query-rewrite-phase16.json` 里 noop 与 deterministic 两列。

**怎么确认。** 能回答：为什么 noop 是必备策略？`changed_queries=0` 时哪些结论是安全的、
哪些是不安全的？如果让 LLM 改写，manifest 里必须记录什么才能让结果可复现
（模型名、prompt、温度、重试次数）？

**例子。** "登录"改写成"登录 登录失败 验证码"看似增加召回词，但也可能稀释 BM25 的
关键信号。学习点：改写是一把双刃剑，任何策略变更都要跑同一 Gold Set 的前后对照。

## Phase 17：Citation

**学到什么。** 引用从"编号正确"升级为"可追溯"：每个 Citation 携带 `chunk_id`（数据库行）、
`score`（检索器原始分）、来源清单 `source_lines()`。学到契约思维：
`citation_valid` 只表示"编号合法且至少引用了一条"，**不代表答案忠实**——
忠实度是 Phase 14 的指标，两个概念不能混用。前端展示需要的"来源区块"
从同一份 Citation 数据派生，不需要额外接口。

**怎么学。** 读 `app/schemas/rag.py` 的 `Citation` 与 `RAGResponse.source_lines`
（去重逻辑：同一来源多个 Chunk 只显示一次），对照 review Phase 17 的记录。

**怎么确认。** 发起一次真实提问，检查响应里 citation 的 `chunk_id` 能否在数据库
查到对应行、`score` 是否与 trace 里的检索分一致；解释 `retriever="hybrid"` 字段的用途。

**例子。** 一个答案引用了同一文档的 3 个 Chunk，`source_lines()` 输出 1 行。
学习点：Chunk 级引用给调试用，来源级展示给用户用，同一数据两种投影。

## Phase 18：完整 RAG Pipeline

**学到什么。** 把 Phase 15/9/11/3 串成一条链并逐阶段留痕：understanding → rewrite →
query embedding → hybrid → rerank → context → llm，每阶段记录候选身份与耗时
（`PipelineTrace`）。学到可观测性的最小形态：**每个阶段的输入输出都可 JSON 化**，
badcase 归因（Phase 19）直接吃 trace。以及"可选接入"的发布策略——新链路先并行提供
端点（`/rag/answer/pipeline`），不直接替换线上默认路径。

**怎么学。** 读 `app/generation/pipeline.py`（`_answer` 的阶段划分、
`_offline_vector_results` 为什么只用于离线、候选深度 `max(top_k*2, 10)` 的原因），
对照 `tests/test_rag_pipeline.py` 与 `tests/api/test_rag_api.py` 的 Pipeline 端点测试。

**怎么确认。** 调 `POST /api/v1/rag/answer/pipeline`，对照 trace 回答：
understanding 提取了什么 filters？fusion_hits 与 rerank_hits 的顺序差异说明什么？
哪个阶段最耗时？能从 trace 复述"这条答案是怎么来的"即通过。

**例子。** trace 显示 `filters={"module": "login"}` 后 `fusion_hits` 里只有 login 模块。
学习点：逐阶段 trace 让"答案错误"变成可定位的工程问题，而不是一句"模型不行"。

## Phase 19：Badcase 闭环

**学到什么。** 错误管理的完整生命周期：九阶段分类（parser / chunk / embedding / query /
retrieval / rerank / context / generation / citation）→ 状态机
（open → fixing → fixed → verified）→ 回归绑定（每条 badcase 必须关联至少一条
回归 query，修复后必须复跑并附 `evaluation_result` 才能 verified）。学到：
跳过评测直接 verified 是流程漏洞，代码层面禁止。

**怎么学。** 读 `app/evaluation/badcase.py` 的 `advance`（状态推进约束）与
`app/services/badcase_service.py`（持久化如何复用契约校验）。用 API 实操：
`POST /api/v1/badcases` 建一条，连续 `advance` 三次观察状态流转与失败分支。

**怎么确认。** ①把一条 badcase 推到 fixed 但不带评测结果，确认被拒；
②回答：为什么回归 query 是必填的？（防止"修好了"变成无法验证的口头承诺）
③对照 Phase 13/14 的 badcase 字符串，说明哪些还没有自动映射到九分类。

**例子。** "BM25 分词未保留标识符"归因 retrieval 阶段，修复后必须用包含
`SHOP-LOGIN-001` 的 query 复跑检索评测。学习点：badcase 是活的工单，
不是日志里的一次性记录。

## Phase 20：性能统计

**学到什么。** 分位数（nearest-rank P50/P95/P99）为什么优于平均值——
平均数会被少数快请求拉低，P95 才是"最差 5% 用户"的体验；分阶段计时
（embedding / retrieval / llm 各自的耗时）才能定位瓶颈。学到契约与实测的边界：
统计工具已就绪，但**没有真实压测就没有 SLO 结论**。

**怎么学。** 读 `app/evaluation/performance.py`（nearest-rank 实现与并发结果契约），
再看 `PipelineTrace.stage_seconds` 如何在 Phase 18 被填充。对照 review Phase 20
理解"未跑真实压测"为什么被显式记录。

**怎么确认。** 手算：10 个请求耗时 [1,1,1,2,2,2,3,5,8,15] 秒，nearest-rank 的
P50 / P90 是多少？再从一次 pipeline trace 里找出最慢阶段并解释优化顺序。

**例子。** 平均耗时 3 秒的服务，P95 可能是 12 秒——如果 SLO 定在 P95<10s，
平均值达标也会违约。学习点：容量规划和告警阈值都必须基于分位数。

## Phase 21：增量索引

**学到什么。** 内容哈希驱动的最小变更：期望状态（来源实体 + 正文 hash）对比库内
`content_hash`，生成 CREATE / UPDATE / DELETE 计划，执行与计划解耦。学到三个工程决策：
①决策（hash 对比）与执行（写库 + Embedding）分离，dry-run 才可能；
②**DELETE 检测的范围必须显式声明**——从"期望列表"推导范围时，全部删除的实体会被漏掉
（这是本项目 review 抓出的真实 P1）；③复跑无变化时零写入（幂等）。

**怎么学。** 读 `app/ingestion/incremental.py`（`plan_incremental_index`）与
`app/services/incremental_index_service.py`（scope 修复的注释），对照
`review/review-step3-incremental-executor-*.md` 的完整分析。
跑：`uv run python scripts/incremental_index.py --dry-run`，改动一个用例标题后再跑对比差异。

**怎么确认。** ①连续跑两次同步，第二次应全是 unchanged；②删除一条实体后再同步，
确认它的 Chunk 被清理；③回答：为什么执行器不接受"从 desired 推导 scope"？

**例子。** 改了一个用例的预期结果：计划输出 `update=1 unchanged=N`，只有这一条
重新生成向量。学习点：Embedding 是最贵的操作，增量化的本质是"按内容是否变化付费"。

## Phase 22：版本管理

**学到什么。** 知识库的版本维度：`v2.10` 按数值大于 `v2.9`（字符串比较会错）。
实现技巧：`version_key` 零填充（`v2.10` → `0002.0010.0000`），让 SQL 普通字符串
比较等价数值比较；非法版本入 NULL，**不匹配任何区间**（与 `VersionRange.contains`
拒绝 None 的语义一致）。学到"排序键"这一通用模式：凡是 SQL 不擅长的比较，
都可以预计算一个可排序的物化列。

**怎么学。** 读 `app/retrieval/versioning.py`（`parse_version` / `version_key`）与
`alembic/versions/0004_knowledge_chunks_version_key.py`（加列 + 存量回填）。
跑：`uv run python -c "from app.retrieval.versioning import version_key; print(version_key('v2.10'))"`。

**怎么确认。** ①手算 `version_key("v1.2.3")` 并验证；②插入三条不同版本的 Chunk，
用 `VersionRange(min_version="v2.10", max_version="v2.10")` 检索，确认只有目标版本命中
且无版本的 Chunk 不出现；③回答：为什么非法版本宁可排除也不当满足处理？

**例子。** 用户问"v2.10 的登录用例"，字符串过滤 `version <= "v2.9"` 会错误包含 v2.10。
学习点：版本号看起来是字符串，语义上是三位数，物化键让数据库"懂"这一点。

## Phase 23：检索层权限

**学到什么。** 权限必须在**检索层**过滤，不能在展示层隐藏：先召回再删除的话，
Top-K 名额已被无权内容占用（候选饥饿），分数分布也会泄露被隐藏内容的存在。
实现：chunk metadata 的 `access` 块（teams / projects / min_role）翻译成 SQL 谓词，
维度缺省视为"不限"，**畸形配置 fail-closed**（宁可漏召回不泄露）。学到：
SQLite 上 SQL 层只排畸形块、Python 复核兜底的双层防御——方言能力差异下语义仍要一致。

**怎么学。** 读 `app/retrieval/access_filters.py`（每条谓词的
"无 access OR 允许"结构、为什么 `access` 缺失即可见）与
`app/retrieval/access_control.py` 的纯逻辑层，对照
`review/review-step4-version-access-prefilter-*.md` 里的 P0 修复过程。

**怎么确认。** ①构造 `{"access": {"min_role": "editor"}}` 的 Chunk，用 viewer 检索
确认不可见、editor 检索可见；②把 access 写成字符串 `"public"`（畸形），确认对
admin 也不可见；③回答：为什么 display 层过滤（返回后再删）不可接受？

**例子。** 一个 Chunk 只允许 platform 团队可见，qa 团队检索时它在 SQL 阶段就被排除，
连"它存在"这个信息都不会出现在分数列表里。学习点：权限过滤的正确性指标是"无信息泄露"，
不只是"看不见"。

## Phase 24：Dashboard

**学到什么。** 多份实验 manifest 的聚合投影：每个指标行都保留来源
（哪个文件、candidate 还是 formal），缺失实验显式标记"缺失"而不是留空或编造。
学到数据产品的诚实原则：**聚合层不生产数据，只搬运并标注来源**；
没有 UI 也有价值——API/JSON 层的聚合已经能回答"我们现在知道什么、还不知道什么"。

**怎么学。** 读 `app/evaluation/dashboard.py` 的聚合结构与溯源字段，
对照 `results/` 下的真实 manifest 文件，练习"从聚合结果反查原始 manifest"。

**怎么确认。** ①给出当前所有 retrieval 相关指标及其来源文件与模式标注；
②找到至少一个"缺失行"并解释它缺什么（如正式 rerank 实验）；
③回答：为什么聚合层拒绝把 candidate 结果显示成 formal？

**例子。** 实验矩阵里 `rerank` 行标注"缺失"，因为 Phase 11 的正式实验还没跑。
学习点：缺失的诚实呈现比占位数有价值，占位数据迟早被当成真数据引用。

## Phase 25：Agentic RAG

**学到什么。** Agent = Planner（决定调什么工具）+ 工具白名单（受控执行）+ 受限循环
（max_steps）。两条铁律：①Planner 只能选工具和参数，不能直接写答案或编造来源；
②工具是唯一证据入口，每个工具结果强制带 `source_type:source_id` 前缀——
无来源的文本不算证据。学到：Agent 的价值在"多步检索"（先查缺陷再查相关用例），
而不是把 LLM 包成万能回答器。

**怎么学。** 读 `app/agent/tools.py`（`ToolRegistry` 的拒绝逻辑、`AgentLoop` 的
trace 记录）与 `app/agent/retrieval_tools.py`（每个工具如何把检索结果转成带来源的证据）。
对照 `tests/test_agent_retrieval_tools.py` 理解"失败转 ToolResult 而不是抛异常"的设计。

**怎么确认。** ①用 `ToolCall(tool="不存在的工具")` 确认返回错误列表而不是异常；
②检查任一工具输出的证据字符串都带来源前缀；③回答：为什么 max_steps 是硬约束
而不是建议值？

**例子。** "登录缺陷影响哪些用例？"由 Planner 规划两步：`search_defects("登录缺陷")`
→ `search_testcases(相关模块)`，两步的证据都带业务编号。学习点：
把 Agent 的自由度限制在"选工具"，证据质量由工具的白名单和来源标识保证。

## Phase 26：回归测试 Agent

**学到什么。** 自动化与人工审批的交界：Agent 根据四类证据（badcase、缺陷、变更、
回归需求）生成**计划草稿**，但执行前必须有 Terry 的显式批准——这是代码里的硬门槛。
学到人机协作的接口设计：机器负责穷举和建议（它擅长），人负责批准和担责（他必须）。

**怎么学。** 读 `app/agent/regression_planner.py` 的计划生成与批准字段，
对照 review Phase 26 记录的"批准是硬门槛"决策。联系 Phase 19：批准后的计划
就是 badcase 闭环的 verified 依据。

**怎么确认。** ①生成一份计划草稿，检查每项是否带证据引用；②确认未批准状态
无法进入执行；③回答：为什么不让 Agent 自动批准"低风险"回归？

**例子。** 缺陷 BUG-001 修复后，Agent 草拟"复跑 BUG-001 相关的 3 条回归 query"，
Terry 批准后才执行。学习点：自动化的产出如果直接生效，错误也会自动生效。

## Phase 27：实验矩阵

**学到什么。** 跨实验的全景视图：Chunking × Embedding × Hybrid × Rerank × Filter
各维度的最优组合是什么、哪些格子有数据、哪些缺失。学到实验管理的方法论：
**每次实验固定其他变量，矩阵记录每次实验的变量取值**，缺格子的地方就是下一步实验计划。

**怎么学。** 读 `app/evaluation/experiment_matrix.py` 的聚合逻辑与缺失标记，
对照 `results/` 下各 manifest 的参数字段（top_k、fusion、k1/b 等），
理解矩阵如何从 manifest 自动生成而不是手填。

**怎么确认。** ①画出现有矩阵（可手绘）：哪些格子有 candidate 数据、哪些是空的；
②指出补齐 rerank 格子需要什么前提（可用 reranker + 人工 Gold Set）；
③回答：为什么矩阵行缺失要报错/标记而不是跳过？

**例子。** "固定 chunking=entity+heading，换 embedding 模型"是一行；
该行有三个模型的数据，但全部是 candidate 模式——矩阵让这个局限一眼可见。

## Phase 28：架构文档

**学到什么。** 单篇架构总览 + 分专题报告的文档结构：`docs/ARCHITECTURE.md` 讲模块
关系与数据流，每个专题的实验细节留在各自报告（CHUNKING_EXPERIMENT 等），
每条结论都链接到报告或 review。学到：文档的验收标准是"新人能从文档走到代码"，
以及"结论必须有出处"——没有报告支撑的性能声明等于没说。

**怎么学。** 通读 `docs/ARCHITECTURE.md`，对每个模块找到对应的代码文件与报告；
再挑一个结论（如"过滤必须 LIMIT 前执行"）追溯它的报告、代码、测试三层证据。

**怎么确认。** ①合上文档画出系统数据流（导入 → 切分 → 索引 → 检索 → 生成 → 评测）；
②任选一条指标说出它的 manifest 路径与生成命令；③找出文档中所有标注
"未验证 / 局限"的地方——它们与结论同等重要。

**例子。** `ARCHITECTURE.md` 里"Pipeline 未替换默认 /rag/answer"这一句让读者不会
误以为新链路已在生产生效。学习点：文档不仅写"有什么"，还要写"还没什么"。

## Phase 29–30：计划映射与最终验收

**学到什么。** 两课收尾：①8 周计划逐项映射到 Phase，验证排期的完成度
（`docs/PHASE_PROGRESS.md` 的 Phase 29 行）；②最终验收的诚实声明——
工程组件可以完成，但**正式指标验收被 Phase 12 的人工确认阻塞**（总闸门）。
学到项目管理的最后一块拼图：区分"工程完成"与"质量验收完成"，
后者的前置条件是人的判断。

**怎么学。** 通读 `docs/PHASE_PROGRESS.md` 的 Phase 29–30 行与
"当前仍差什么"两表（工程缺口 / 人工缺口），对照 `docs/LEARNING_QUESTIONS.md`
逐条自评：10 个学习问题里哪些已有 candidate 答案、哪些完全没有数据。

**怎么确认。** ①列出所有"人工缺口"及其解锁条件（例如：确认 Phase 12 → 解锁
Phase 13/14 formal 重跑）；②解释为什么 AI 不能批量把 pending 改成 confirmed；
③给自己定一个 8 周后的检查点：到时哪些格子应该被填上。

**例子。** Phase 30 的状态是"阻塞于人工门槛"而不是"未完成"——语义完全不同：
前者说明工程侧已就绪、在等输入，后者说明工作未做。

---

## 建议学习节奏（8 周）

| 周 | 内容 | 里程碑 |
|---|---|---|
| 1 | Phase 0–2 + 环境 | 数据库跑通，向量距离手算过 |
| 2 | Phase 3–5 | 亲手导入一个文档并走通最小 RAG |
| 3 | Phase 6–7 | 跑完 chunking 与 embedding 实验，读懂两份报告 |
| 4 | Phase 8–10 | 手算 RRF，预测并通过三组过滤场景 |
| 5 | Phase 11–12 | 完成 rerank dry-run + 人工审阅 10 条 Phase 12 |
| 6 | Phase 13–14 | 手算四个指标；构造四个生成评测 case |
| 7 | Phase 15–18 | 跑通 understanding/rewrite 对比 + pipeline trace 分析 |
| 8 | Phase 19–25 | 完成 badcase 闭环实操 + Agent 工具调用 + 矩阵自查 |

**通用自测清单（任何时候都能用）**

- 给我一条 query，我能说出它在这条链路上的完整旅程。
- 任何一个指标数字，我能说出它的 manifest 来源与生成命令。
- 任何一个工程限制（如"SQLite 无向量检索"），我能说出它守住的诚实边界。
- 任何一个"未完成"，我能说出它阻塞在工程还是人工。
