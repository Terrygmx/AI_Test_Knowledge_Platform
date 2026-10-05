# AI Test Knowledge Platform：测试工程知识智能平台 V2

## 1. 项目定位

**项目名称：** AI Test Knowledge Platform（测试工程知识智能平台）

本项目不是简单的"测试知识库聊天机器人"，而是面向测试工程的知识基础设施：统一接入测试用例、缺陷、需求/产品文档和测试规范，通过结构化知识建模、Hybrid
Retrieval、Rerank、Query Understanding 和
RAG，为测试人员以及后续智能回归测试 Agent 提供可追溯的知识能力。

核心目标：

1.  掌握企业级 RAG 的完整工程链路；
2.  在真实测试业务中落地；
3.  建立可量化的 RAG Evaluation 和 Badcase 优化体系；
4.  最终与智能回归测试 Agent 打通。

## 2. 核心原则

-   **Retrieval First：** 先证明检索正确，再做生成。
-   **结构化优先：** TestCase、Defect、Requirement 不能简单当作普通文本
    Chunk。
-   **Hybrid Retrieval：** Vector + Keyword + Metadata Filter。
-   **Rerank：** 用于提升候选证据排序质量。
-   **Evidence First：** 回答必须有来源，不能凭空生成 Case ID / Bug ID。
-   **Evaluation First-class：** 每次修改
    Chunk、Embedding、TopK、Rerank、Query Rewrite 或 Prompt
    都可以重新评测。
-   **LLM 可替换：** LLM、Embedding、Reranker 均通过接口抽象。

## 3. 典型场景

### 测试用例检索

"登录模块 P0 的短信登录冒烟用例有哪些？"

解析为：

``` text
intent = testcase_search
module = login
priority = P0
type = smoke
semantic_query = 短信登录
```

执行：

``` text
Metadata Filter
+ BM25 / Full Text Search
+ Vector Search
→ Hybrid Fusion
→ Rerank
→ Evidence
```

### 缺陷检索

"支付失败相关的 P0 缺陷最近怎么处理？"

### 回归辅助

"V1.3 支付模块上线前需要回归哪些测试？"

综合当前需求、历史缺陷、测试用例、回归清单和测试规范，输出推荐测试集合并标注来源。

### 版本分析

"V1.2 到 V1.3 支付相关测试发生了什么变化？"

## 4. 总体架构

``` text
User / Test Agent
       |
       v
Query Understanding
       |
       v
Retrieval Planner
       |
  +----+----+----+
  |    |    |    |
Meta BM25 Vector
  |    |    |
  +----+----+
       |
Hybrid Fusion
       |
Rerank
       |
Evidence
       |
 +-----+------+
 |            |
Search      RAG/Agent
 |            |
 |       Answer + Citation
 +-----+------+
       |
Evaluation
       |
Badcase
       |
Optimization
       |
Regression Evaluation
```

## 5. 数据模型

### TestCase

``` text
id, case_id, title, module, sub_module, version,
priority, type, precondition, steps, expected_result,
tags, source, created_at, updated_at
```

### Defect

``` text
id, bug_id, title, module, version, severity,
priority, status, reproduce_steps, root_cause,
resolution, related_cases, created_at, updated_at
```

### KnowledgeChunk

``` text
id, source_type, source_id, document_id, chunk_index,
content, metadata, embedding, content_hash, version
```

同时保存原始业务实体与检索表示，不把 Chunk 当成唯一事实来源。

## 6. 推荐技术栈

``` text
Python 3.11+
FastAPI
PostgreSQL
pgvector
Pydantic
SQLAlchemy
Alembic
PostgreSQL Full Text Search / BM25
bge-m3 或同级 Embedding
bge-reranker 或同级 Reranker
OpenAI-compatible LLM API
Pytest
Docker Compose
Streamlit / Gradio
```

V1 不建议一开始引入 Milvus。测试数据具有强结构化特征，PostgreSQL
可以同时承担关系数据、Metadata、全文检索和 Vector。pgvector 支持 exact /
approximate nearest-neighbor search、HNSW、IVFFlat，并可与 PostgreSQL
Full Text Search 组合实现 Hybrid Search。

## 7. RAG Pipeline

``` text
Source
→ Parser
→ Normalizer
→ Knowledge Model
→ Chunker
→ Metadata
→ Embedding
→ Index

Query
→ Query Understanding
→ Metadata Filter
→ Vector Search + Keyword Search
→ Hybrid Fusion
→ Rerank
→ Evidence
→ LLM
→ Answer + Citation
```

测试用例原则上"一条用例一个检索单元"；缺陷"一条缺陷一个知识单元"；Markdown/PDF
优先按标题、段落、表格切分，固定长度作为兜底。

## 8. Query Understanding

将自然语言转换为结构化查询：

``` json
{
  "intent": "testcase_search",
  "module": "payment",
  "priority": "P0",
  "version": "1.3",
  "keywords": ["支付失败"],
  "semantic_query": "支付失败回归"
}
```

LLM 负责理解，不直接决定最终证据。

## 9. Evaluation

建立约 100 条人工确认的 Evaluation Dataset：

``` text
TestCase Search 30
Defect Search 20
Document QA 20
Cross Version 10
Multi-condition 10
Unanswerable 10
```

Retrieval：

-   Recall@K
-   Precision@K
-   MRR
-   NDCG@K

Generation：

-   Faithfulness
-   Answer Relevancy
-   Citation Correctness

System：

-   P50/P95/P99
-   Token usage
-   Embedding cost
-   Rerank latency
-   Error rate

## 10. Badcase 闭环

``` text
Badcase
→ Failure Stage
→ Root Cause
→ Hypothesis
→ Fix
→ Evaluation
→ Regression
```

分类：

``` text
Ingestion
Parsing
Chunking
Embedding
Query Understanding
Retrieval
Rerank
Context Assembly
Generation
Citation
```

## 11. MVP

必须完成：

-   TestCase / Defect / Markdown 接入
-   PostgreSQL + pgvector
-   Embedding
-   Vector Search
-   Full Text Search
-   Hybrid Search
-   Metadata Filter
-   Rerank
-   Citation
-   FastAPI
-   100 条 Evaluation Dataset
-   Recall@K / MRR
-   Badcase
-   README / 架构图 / Evaluation Report

第二阶段：

-   Query Understanding
-   Query Rewrite
-   增量索引
-   内容 Hash
-   版本管理
-   权限
-   Dashboard
-   Evaluation 自动化

第三阶段：

-   Agentic RAG
-   Test Agent Tool Calling
-   测试经验沉淀

## 12. 与智能回归测试 Agent 的关系

``` text
AI Test Platform
       |
 +-----+------+
 |            |
RAG       Regression Agent
 |            |
Knowledge   Planning
Defects     Execution
Cases       Verification
Requirements
 |            |
 +-----+------+
       |
Test Experience
       |
Knowledge Store
```

RAG 是知识层，Test Agent 是执行层，Evaluation 是质量控制层，Experience
是持续积累的知识资产。

## 13. 成功标准

初始目标：

``` text
Recall@5          >= 85%
MRR               >= 0.80
Citation Accuracy >= 90%
Faithfulness      >= 90%
P95 Latency       < 5s
```

这些是项目初始目标，不是行业统一标准；最终阈值应由真实数据集校准。

## 14. 最终成果

``` text
ai-test-knowledge/
├── app/
│   ├── api/
│   ├── ingestion/
│   ├── retrieval/
│   ├── rerank/
│   ├── generation/
│   ├── evaluation/
│   └── models/
├── data/
├── eval/
├── scripts/
├── tests/
├── docker-compose.yml
├── README.md
├── ARCHITECTURE.md
├── EVALUATION.md
└── CHANGELOG.md
```

最终展示：架构图、数据模型、RAG Pipeline、Retrieval 对比实验、Evaluation
Report、Badcase、优化前后数据、Demo、与 Test Agent 的集成方案。

## 15. 简历表述

> 设计并落地面向测试工程的 AI Knowledge/RAG
> Platform，统一接入测试用例、缺陷、需求及测试规范，构建 PostgreSQL +
> pgvector + Full Text Search 的 Hybrid Retrieval Pipeline，并结合
> Rerank、Metadata Filter、Query Understanding
> 实现测试知识检索与可追溯问答；建立包含
> Recall@K、MRR、Faithfulness、Citation Correctness、P95 Latency
> 的离线评测体系，通过 Badcase → Root Cause → Optimization → Regression
> Evaluation 闭环持续优化检索质量，并为智能回归测试 Agent
> 提供知识检索与上下文能力。
