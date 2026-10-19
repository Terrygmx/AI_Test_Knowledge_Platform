# AI Test Knowledge Platform：项目范围

> 本文定义 MVP 的边界，回答“解决什么问题、服务谁、做什么、不做什么、如何判定成功”。
> 后续新增需求先与本文对照；超出边界的能力进入 Roadmap，不直接塞进当前迭代。

## 1. Problem

测试工程知识分散在测试用例、缺陷、需求 / 产品文档、测试规范和回归记录中。
这些数据具有强结构化和强关联特征，但当前检索通常依赖文件名、人工记忆或简单关键词，
难以回答同时包含业务语义和结构化条件的测试问题。

本项目要解决的核心问题是：

> 如何让测试人员和智能测试 Agent 在**可追溯**的前提下，快速找到覆盖当前版本、
> 模块、优先级和历史缺陷的正确证据？

典型问题不是“泛泛介绍登录测试”，而是：

- 登录模块 P0 的短信登录冒烟用例有哪些？
- 支付失败相关的高优先级缺陷最近如何修复？
- V1.3 支付模块上线前应回归哪些用例和需求？
- V1.2 到 V1.3 的支付测试范围发生了什么变化？

## 2. Users

### 主要用户

- 测试工程师：查找用例、缺陷、需求和规范，辅助测试设计与回归分析。
- 测试负责人：确认版本范围、风险覆盖和回归集合。
- 智能回归测试 Agent：通过稳定接口获取带有来源标识的知识证据。

### 当前不做用户体系

MVP 不实现登录、组织、项目、团队和权限模型。权限过滤在 Phase 23 引入，
且必须发生在 Retrieval 层，而不是召回后隐藏结果。

## 3. Data

### 原始业务实体

- `TestCase`：用例编号、模块、版本、优先级、类型、前置条件、步骤、预期结果、标签。
- `Defect`：缺陷编号、模块、版本、严重程度、状态、复现步骤、根因、解决方案、关联用例。
- `Requirement`：需求编号、模块、版本、优先级、状态、正文。
- `Document`：文档元数据、类型、路径、内容哈希、版本。

原始实体是事实来源，不直接等同于检索单元。

### 检索表示

- `KnowledgeChunk`：从原始实体或文档切分得到的检索单元。
- 保存来源类型、来源编号、Chunk 序号、正文、Metadata、内容哈希、版本和向量。
- Embedding 可重建，原始实体与 Chunk 才是需要长期保存的一等数据。

## 4. Use Cases

### MVP 必须支持

1. 测试用例检索：业务语义 + 模块 / 优先级 / 类型 / 版本过滤。
2. 缺陷检索：问题描述 + 模块 / 严重程度 / 状态 / 版本过滤。
3. 需求与文档检索：按标题、正文和 Metadata 找回证据。
4. 跨实体统一检索：一次查询返回不同来源，并说明每条结果来自哪里。
5. 向量检索：给定 Query Embedding，按 cosine distance 返回候选知识单元。
6. 可追溯输出：最终答案只引用系统中真实存在的 Case ID / Bug ID / Req ID / Doc ID。

### MVP 之后

- Hybrid Search：Vector + BM25 / 全文检索 + Metadata Filter。
- Rerank、Query Understanding、Query Rewrite。
- Evaluation Dataset、Recall@K、MRR、Faithfulness、Citation Correctness。
- Badcase 分类与回归评测。
- 版本对比、增量索引、权限、Dashboard。
- Agentic RAG 与智能回归测试 Agent 集成。

## 5. MVP Boundary

### Retrieval 与 Generation 的边界

- Retrieval 负责把正确、可解释的证据找出来。
- Generation 只能基于 Evidence 组织答案，不允许创造来源标识。
- 无法找到足够证据时，系统必须明确回答“证据不足”。
- 没有打分依据时不返回伪造的 `score`；引入打分后必须说明 retriever、rank 和 score 来源。

### RAG 与 Chatbot 的边界

本项目不是开放域聊天机器人。它优先保证测试知识检索、结构化过滤、来源追踪和离线评测；
自然语言生成是检索链路稳定后的上层能力。

### POC / MVP / Production

- POC：验证 Embedding、Vector Search 和最小 RAG 可行。
- MVP：覆盖核心实体、Hybrid Retrieval、Citation 和人工确认的评测集。
- Production：补齐权限、增量索引、可观测性、性能目标和故障恢复。

当前项目处于 MVP 建设阶段，优先完成可解释的 Retrieval Pipeline，不以 Demo 效果代替评测。

## 6. Non-goals

当前阶段明确不做：

- 引入 Milvus 等多套向量存储；PostgreSQL + pgvector 先满足 MVP。
- 一开始支持所有 PDF、OCR、复杂表格和版面还原。
- 使用 LangChain / LlamaIndex 隐藏检索链路；核心 Pipeline 手写。
- 用 LLM 直接决定最终证据或伪造引用编号。
- 为追求“高级”而保留没有评测收益的 Query Rewrite / Agent 能力。
- 在 Retrieval 稳定前投入大量 UI / Dashboard 开发。
- 训练或微调自有 Embedding / LLM。

## 7. Success Metrics

初始目标由真实评测集校准，当前作为方向而非行业标准：

| 指标 | 初始目标 |
|---|---:|
| Recall@5 | >= 85% |
| MRR | >= 0.80 |
| Citation Accuracy | >= 90% |
| Faithfulness | >= 90% |
| P95 Latency | < 5s |

工程约束：

- 默认测试不依赖 Docker 和外部 API。
- PostgreSQL 专属能力必须有真实 PG 集成验证。
- 每次修改 Chunk、Embedding、TopK、Rerank 都应能复跑同一套 Evaluation。
- Badcase 必须能定位到 Ingestion / Chunking / Retrieval / Rerank / Generation / Citation 等阶段。

## 8. Current Phase Mapping

| 阶段 | 状态 | 产出 |
|---|---|---|
| Phase 0 | 完成 | 项目范围、学习问题 |
| Phase 1 | 完成 | FastAPI、四实体、Alembic、PostgreSQL 实机验证 |
| Phase 2 | 进行中 | `knowledge_chunks`、Embedding、pgvector 向量检索 |
| Phase 3 | 待开始 | 手写最小 RAG |

### Phase 2 验证边界

`FakeEmbeddingClient` 只用于验证“实体 → Chunk → 向量 → pgvector → 距离返回”链路，
不提供真实语义能力。Embedding API Key 配置后，必须用 bge-m3 在真实测试 Query 上重新验证
相似文本是否更接近；不能把 Fake 结果当作召回质量结论。

## 9. Human Review Points

以下判断需要项目负责人确认，而不是由代码实现默认替代：

- MVP 的“正确检索”由哪些真实业务 Query 定义。
- Gold Evidence 的标注口径：实体级、Chunk 级还是两者并存。
- 测试数据中版本、模块、优先级词表的业务规范。
- Embedding / Reranker 模型和成本可接受范围。
- 首版 Citation 展示粒度和“证据不足”的判定阈值。
