# AI Test Knowledge Platform：详细实现与学习计划

## 0. 学习策略

核心原则：**AI 可以替你写代码，但不能替你做技术判断。**

  工作                    个人    AI
  --------------------- ------ -----
  架构决策                 90%   10%
  技术选型                 80%   20%
  RAG 原理                 80%   20%
  SQL / pgvector 原理      70%   30%
  Retrieval 算法           70%   30%
  CRUD / API 样板          30%   70%
  测试数据生成             20%   80%
  单测初稿                 30%   70%
  Debug                    60%   40%
  Evaluation 分析          70%   30%
  Badcase 根因             80%   20%
  README 初稿              30%   70%
  最终技术结论             90%   10%

必须自己能够回答：为什么需要 Chunk、Embedding、Hybrid
Search、Rerank、Evaluation，以及为什么某个 Badcase 是 Retrieval Error
而不是 Generation Error。

------------------------------------------------------------------------

# Phase 0：项目边界

## 0.1 写 PROJECT_SCOPE.md

掌握：

-   RAG 与 Chatbot 的区别
-   Retrieval / Generation 边界
-   MVP / POC / Production

内容：

``` text
Problem
Users
Data
Use Cases
MVP
Non-goals
Success Metrics
```

投入：个人 90%，AI 10%。

## 0.2 建立 LEARNING_QUESTIONS.md

至少回答：

1.  Embedding 表示什么？
2.  Chunk 为什么影响 Recall？
3.  Vector Search 与 BM25 的区别？
4.  Reranker 为什么有用？
5.  TopK 怎么选？
6.  Recall@K / MRR 怎么算？
7.  Faithfulness 与 Recall 有什么区别？
8.  RAG 为什么会幻觉？
9.  什么情况下不应该用 RAG？
10. RAG 如何持续优化？

投入：个人 80%，AI 20%。

------------------------------------------------------------------------

# Phase 1：FastAPI + PostgreSQL

## 1.1 FastAPI

掌握：

-   Router
-   Pydantic
-   Dependency Injection
-   Exception Handler
-   Middleware
-   Async/Await
-   OpenAPI
-   API Testing

产出：

``` text
GET /health
POST /testcases
GET /testcases/{id}
POST /defects
GET /search
```

参考：FastAPI 官方 Tutorial、Pydantic 官方文档。

AI：70% 可交给 AI，但自己必须理解 API 生命周期。

## 1.2 PostgreSQL

掌握：

-   Table
-   Primary/Foreign Key
-   Index
-   JOIN
-   JSONB
-   Full Text Search
-   Transaction
-   EXPLAIN

设计：

``` text
test_cases
defects
requirements
documents
knowledge_chunks
evaluation_cases
evaluation_results
badcases
```

AI：SQL 初稿可 70% 交给 AI；Schema 必须自己设计。

------------------------------------------------------------------------

# Phase 2：Embedding + pgvector

## 2.1 Embedding 实验

自己写：

``` text
sentence
→ embedding
→ cosine similarity
```

比较：

``` text
支付失败
支付错误
登录成功
```

观察相似度。

投入：个人 80%。

## 2.2 pgvector

掌握：

-   vector type
-   cosine distance
-   exact search
-   HNSW
-   IVFFlat
-   filtering
-   Hybrid Search

重点阅读：pgvector 官方 GitHub README。它明确说明 HNSW/IVFFlat
的速度-召回权衡、过滤对近似检索的影响，以及 PostgreSQL Full Text
Search + RRF / cross-encoder 的 Hybrid Search 做法。

产出：

``` sql
SELECT ...
ORDER BY embedding <=> query_embedding
LIMIT 10;
```

AI：SQL 60%，原理 100% 自己掌握。

------------------------------------------------------------------------

# Phase 3：手写最小 RAG

不要先上 LangChain/LlamaIndex。

``` text
Text
→ Chunk
→ Embedding
→ PostgreSQL
→ Vector Search
→ Prompt
→ LLM
```

掌握：

-   Context Window
-   Prompt
-   TopK
-   Context Assembly
-   Grounding
-   Citation

产出：

``` bash
python ingest.py
python query.py "登录失败怎么测试？"
```

AI：编码 50%，Pipeline 思路自己设计。

------------------------------------------------------------------------

# Phase 4：测试知识模型

## 4.1 TestCase

字段：

``` text
Case ID
Module
Version
Priority
Type
Precondition
Steps
Expected
Tags
```

## 4.2 Defect

``` text
Bug ID
Module
Severity
Priority
Status
Version
Reproduce Steps
Root Cause
Resolution
Related Cases
```

## 4.3 KnowledgeChunk

理解：

> 原始业务实体 ≠ Retrieval Chunk。

保存：

``` text
raw entity
retrieval representation
metadata
embedding
```

产出：`docs/WHY-STRUCTURED-KNOWLEDGE.md`

个人 80%。

------------------------------------------------------------------------

# Phase 5：数据接入

## 5.1 Excel / CSV

掌握：

-   pandas
-   schema validation
-   encoding
-   null
-   cleaning
-   deduplication

AI：80%。

## 5.2 Markdown

掌握：

-   Heading
-   Paragraph
-   Table
-   Code Block
-   Metadata

实现：

``` text
Markdown
→ Document Tree
→ Sections
→ Chunks
```

## 5.3 PDF

第一版只支持普通文本 PDF；后续再做表格/OCR/Layout。

原则：不要一开始追求"支持所有 PDF"。

------------------------------------------------------------------------

# Phase 6：Chunking 实验

比较：

``` text
Fixed 256
Fixed 512
Fixed 800
Fixed 1200
Semantic
TestCase-per-Chunk
```

在同一 Evaluation Dataset 上计算：

``` text
Recall@5
Recall@10
MRR
```

产出：`docs/CHUNKING_EXPERIMENT.md`

个人 80%，AI 负责实验脚本、统计和表格。

> 当前状态：已完成小规模模拟语料实验（3 篇文档 / 40 条 Query），结论与局限见
> `docs/CHUNKING_EXPERIMENT.md`；扩大到约 100 条人工确认 Query 后仍需复验。

------------------------------------------------------------------------

# Phase 7：Embedding 实验

比较至少两个 Embedding 方案：

``` text
Embedding A
Embedding B
```

指标：

``` text
Recall@5
Recall@10
MRR
Latency
```

产出：`docs/EMBEDDING_EXPERIMENT.md`

个人 70%。

------------------------------------------------------------------------

# Phase 8：BM25 / Full Text Search

掌握：

-   TF-IDF
-   BM25
-   Inverted Index
-   Lexical Search

核心问题：

为什么：

``` text
P0 BUG-1234 login-api v1.3
```

可能更适合关键词检索，而不是 Vector Search？

------------------------------------------------------------------------

# Phase 9：Hybrid Search

实现：

``` text
Vector Top 20
+
Keyword Top 20
→ Fusion
```

先实现 Weighted Score，再实现 RRF。

比较：

``` text
Vector
Keyword
Hybrid
```

指标：

``` text
Recall@5
MRR
Latency
```

产出：`docs/HYBRID_SEARCH_EXPERIMENT.md`

个人 80%。

------------------------------------------------------------------------

# Phase 10：Metadata Filter

支持：

``` text
module
priority
version
status
type
```

重点研究：

-   Filter before retrieval
-   Filter during retrieval
-   Approximate index 下 Filter 对 Recall 的影响

参考 pgvector 官方文档中的 filtering / iterative index scan。

产出：`docs/METADATA_FILTER.md`

个人 80%。

------------------------------------------------------------------------

# Phase 11：Reranker

理解：

``` text
Embedding = fast candidate retrieval
Reranker = slower but more precise relevance scoring
```

Pipeline：

``` text
Vector 20 + BM25 20
→ Fusion
→ 20 candidates
→ Rerank
→ Top 5
```

比较：

``` text
Hybrid
Hybrid + Rerank
```

指标：

``` text
Recall@5
MRR
Latency
```

产出：`docs/RERANK_EXPERIMENT.md`

个人 80%。

------------------------------------------------------------------------

# Phase 12：Evaluation Dataset

建立约 100 条人工确认 Query：

``` text
TestCase Search 30
Defect Search 20
Document QA 20
Cross Version 10
Multi-condition 10
Unanswerable 10
```

每条保存：

``` text
query
expected_intent
expected_filters
gold_documents
gold_chunks
expected_answer_points
```

AI 可以生成候选问题，但关键 Gold Set 必须人工确认。

个人 80%。

------------------------------------------------------------------------

# Phase 13：Retrieval Evaluation

自己实现：

``` python
recall_at_k(retrieved, gold, k)
precision_at_k(...)
mrr(...)
```

理解 NDCG 即可。

目标：不依赖框架也能解释指标。

------------------------------------------------------------------------

# Phase 14：Generation Evaluation

可以引入 Ragas。

重点学习：

-   Faithfulness
-   Answer Relevancy
-   Context Precision
-   Context Recall

Ragas 的定位是把 LLM 应用从人工 vibe check 推向系统化 Evaluation Loop。

注意：不要只会运行 Ragas；必须能够判断"Retrieval 正确但 Answer
错了"的问题边界。

------------------------------------------------------------------------

# Phase 15：Query Understanding

设计：

``` json
{
  "intent": "...",
  "module": "...",
  "priority": "...",
  "version": "...",
  "keywords": [],
  "semantic_query": "..."
}
```

学习：

-   JSON Schema
-   Pydantic
-   Structured Output
-   Retry

AI：编码 70%；Schema 和业务语义自己决定。

------------------------------------------------------------------------

# Phase 16：Query Rewrite

先实现：

``` text
Original Query
→ LLM Rewrite
→ Search Query
```

然后用 Evaluation Dataset 验证：

``` text
Recall 是否提高？
MRR 是否提高？
Latency 增加多少？
```

如果没有收益，不要为了"高级"而保留。

------------------------------------------------------------------------

# Phase 17：Citation

Evidence 保存：

``` text
chunk_id
source_type
source_id
content
score
rank
retriever
```

回答：

``` text
结论……

来源：
- LOGIN-001
- BUG-1021
- requirement/login.md
```

加入"不可回答"测试，要求系统明确表示证据不足。

------------------------------------------------------------------------

# Phase 18：完整 RAG Pipeline

组合：

``` text
Query
→ Understanding
→ Metadata Filter
→ Vector + Keyword
→ Fusion
→ Rerank
→ Evidence
→ LLM
→ Citation
```

每一阶段记录日志：

``` json
{
  "query": "...",
  "filters": {},
  "vector_hits": [],
  "keyword_hits": [],
  "fusion_hits": [],
  "rerank_hits": [],
  "final_context": [],
  "answer": "..."
}
```

这是后续 Badcase 分析的基础。

------------------------------------------------------------------------

# Phase 19：Badcase 系统

分类：

``` text
Parser
Chunk
Embedding
Query
Retrieval
Rerank
Context
Generation
Citation
```

闭环：

``` text
Badcase
→ Root Cause
→ Hypothesis
→ Fix
→ Evaluation
→ Regression
```

个人 90%。

这是你测试开发背景最有优势的部分。

------------------------------------------------------------------------

# Phase 20：性能测试

利用你的测试开发经验。

API：

-   Locust / pytest
-   并发
-   P50/P95/P99

Pipeline profiling：

``` text
Query Understanding
Embedding
Vector Search
BM25
Fusion
Rerank
LLM
Total
```

目标：定位真正瓶颈，而不是只看总响应时间。

------------------------------------------------------------------------

# Phase 21：增量索引

实现：

``` text
content
→ hash
→ 新增 / 修改 / 删除
```

Pipeline：

``` text
Source
→ Detect Change
→ Parse
→ Chunk
→ Embedding
→ Upsert
```

AI：编码 70%；一致性设计自己掌握。

------------------------------------------------------------------------

# Phase 22：版本管理

实现：

``` text
Login TestCase
v1.1
v1.2
v1.3
```

支持：

> V1.2 和 V1.3 登录测试有什么变化？

形成 Version-aware Retrieval。

------------------------------------------------------------------------

# Phase 23：权限

MVP：

``` text
team
project
role
```

关键原则：

> Retrieval 层就应该做权限过滤，而不是召回后再隐藏。

------------------------------------------------------------------------

# Phase 24：Dashboard

展示：

``` text
Queries
Recall
MRR
Badcase Rate
P95
Token Usage
Top Questions
Failure Types
```

不要花大量时间做 UI。

------------------------------------------------------------------------

# Phase 25：Agentic RAG

只有前面的 RAG 稳定后再做。

Tools：

``` text
search_testcases()
search_defects()
search_requirements()
search_regression_history()
compare_versions()
```

流程：

``` text
Question
→ Planner
→ Tool Selection
→ Evidence
→ Reasoning
→ Answer
```

掌握：

-   Tool Calling
-   Planning
-   Observation
-   Evidence
-   Agent Loop
-   Failure Handling

------------------------------------------------------------------------

# Phase 26：连接智能回归测试 Agent

目标：

``` text
“帮我制定支付模块 V1.3 回归测试计划”
```

Agent：

``` text
1. Search requirements
2. Search changed test cases
3. Search P0/P1 defects
4. Search historical regression
5. Generate plan
6. Ask for confirmation
7. Execute approved tests
```

此时 RAG 才真正成为 AI Test Infrastructure。

------------------------------------------------------------------------

# Phase 27：最终实验矩阵

至少比较：

  Experiment                 Recall@5   MRR   P95 结论
  ------------------------ ---------- ----- ----- ------
  Vector                                          
  BM25                                            
  Hybrid                                          
  Hybrid + Rerank                                 
  Hybrid + Query Rewrite                          
  Final                                           

同时做：

``` text
Chunk Size
Embedding Model
TopK
Rerank TopK
```

实验。

------------------------------------------------------------------------

# Phase 28：项目文档

最终：

``` text
README.md
ARCHITECTURE.md
DATA_MODEL.md
RETRIEVAL.md
EVALUATION.md
BADCASE.md
PERFORMANCE.md
CHANGELOG.md
```

每篇只回答一个问题。

------------------------------------------------------------------------

# Phase 29：8 周计划

## Week 1

-   项目 Scope
-   FastAPI
-   PostgreSQL
-   pgvector
-   最小 Vector Search
-   最小 RAG

学习：Embedding、Vector、Cosine Similarity、RAG 基本流程。

## Week 2

-   TestCase
-   Defect
-   Requirement
-   Markdown
-   Excel
-   Chunking

输出 Data Model + Ingestion Pipeline。

## Week 3

-   Vector Search
-   Full Text Search
-   Hybrid Search
-   Metadata Filter

重点：真正理解 Retrieval。

## Week 4

-   Reranker
-   Query Parser
-   Query Rewrite
-   Citation

输出 RAG Pipeline V1。

## Week 5

-   100 Evaluation Queries
-   Recall@K
-   MRR
-   Precision@K
-   Faithfulness
-   Answer Relevancy

输出 EVALUATION.md。

## Week 6

-   Badcase Store
-   Failure Classification
-   Chunk Experiment
-   Embedding Experiment
-   Retrieval Experiment
-   Rerank Experiment

输出 Optimization Report。

## Week 7

-   API
-   Logging
-   Incremental Index
-   Version
-   Performance Test
-   Dashboard

## Week 8

-   Tool Calling
-   Agentic RAG Prototype
-   Test Agent Integration
-   Demo
-   Architecture
-   README
-   Resume Description

------------------------------------------------------------------------

# Phase 30：最终验收

### Retrieval

-   [ ] Vector Search
-   [ ] Keyword Search
-   [ ] Hybrid Search
-   [ ] Metadata Filter
-   [ ] Rerank

### Evaluation

-   [ ] Gold Dataset
-   [ ] Recall@K
-   [ ] MRR
-   [ ] Faithfulness
-   [ ] Citation Correctness
-   [ ] P95

### Engineering

-   [ ] 增量更新
-   [ ] 日志
-   [ ] API
-   [ ] 测试
-   [ ] Docker Compose
-   [ ] 错误处理

### AI

-   [ ] Query Understanding
-   [ ] RAG
-   [ ] Citation
-   [ ] Agentic RAG Prototype

### Documentation

-   [ ] Architecture
-   [ ] Evaluation
-   [ ] Badcase
-   [ ] Optimization
-   [ ] README

最终目标：你可以不依赖任何特定 RAG 框架，独立解释并实现：

``` text
Ingestion
→ Knowledge Modeling
→ Chunking
→ Embedding
→ Vector Search
+ Keyword Search
→ Hybrid Retrieval
→ Metadata Filtering
→ Rerank
→ Evidence
→ LLM
→ Citation
→ Evaluation
→ Badcase
→ Optimization
→ Agent
```
