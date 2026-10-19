# RAG 核心学习问题

> 本文记录项目当前的技术理解，要求在实现每个 Phase 前能脱离框架解释对应概念。
> 答案会随着实验和 Evaluation 结果更新；“项目含义”用于把概念落到当前代码。

## 1. Embedding 表示什么？

Embedding 把文本映射到高维向量空间，使语义相近的文本在距离度量下更接近。
它表示的是模型学到的语义特征，不是事实数据库，也不保证同义、否定、数字和版本号都能被正确区分。

**项目含义：**

- Embedding 是检索表示，不替代原始实体。
- 向量维度、模型版本和归一化方式必须固定并记录。
- 修改 Embedding 模型后必须重建索引并重新评测 Recall / MRR。

## 2. Chunk 为什么影响 Recall？

Chunk 决定“一个可被检索的证据单元有多大”。太大时一个向量混合多个主题，语义被稀释；
太小时上下文不完整，正确片段可能缺少回答问题所需的条件。切分边界还会改变 Metadata 和引用粒度。

**项目含义：**

- 用例和缺陷优先“一实体一 Chunk”；长文档按标题、段落、表格切分。
- Chunk 必须保存来源、序号、版本和内容哈希。
- 固定长度只是兜底，不是默认最优策略。

## 3. Vector Search 与 BM25 的区别？

Vector Search 比较语义向量，擅长同义表达和自然语言描述；BM25 / 全文检索比较词项匹配，
擅长精确编号、专有名词、版本号和关键词。两者失败模式不同，因此需要 Hybrid Search。

**项目含义：**

- “支付失败”可能适合 Vector。
- `BUG-1234`、`P0`、`v1.3` 通常需要关键词 / 精确过滤兜底。
- Hybrid 结果必须保留每个召回通道和融合依据，便于 Badcase 分析。

## 4. Reranker 为什么有用？

Embedding 检索追求快速召回，通常是 bi-encoder，Query 和文档分别编码；
Reranker 通常是 cross-encoder，联合阅读 Query 与候选文本，能更细粒度判断相关性，但速度更慢。

**项目含义：**

- 先用 Vector / Keyword 召回 Top N，再对少量候选 Rerank。
- 评测必须同时看 Recall@K、MRR 和延迟，不能只凭主观排序变好。
- Reranker 不得改变证据来源，只改变候选排序。

## 5. TopK 怎么选？

TopK 是在召回率、上下文长度、延迟和噪声之间做权衡。K 太小可能漏掉 gold evidence，
K 太大则增加 Rerank 成本和生成噪声。正确方法是在固定 Evaluation Dataset 上观察指标曲线。

**项目含义：**

- 初始配置为 Vector Top 20、Keyword Top 20、Final Top 5。
- 这些是实验起点，不是永久常量。
- Chunk、Embedding、过滤方式变化后必须重新调 TopK。

## 6. Recall@K / MRR 怎么算？

- Recall@K：前 K 个结果中命中的相关证据数 / 全部相关证据数。
- MRR：对每个 Query 取第一个相关结果排名的倒数，再对 Query 求平均。

**项目含义：**

- Recall 衡量“有没有找回来”，MRR 衡量“正确结果排得够不够前”。
- Gold Set 必须明确相关证据的粒度，否则指标不可复现。
- 实现指标时不依赖 RAG 框架，确保能解释每个数字。

## 7. Faithfulness 与 Recall 有什么区别？

Recall 评价 Retrieval 是否找全了证据；Faithfulness 评价生成答案是否忠实于给定 Context。
Recall 低时答案可能“编得合理”，Faithfulness 高但不可用；Recall 高时生成仍可能误解或拼接错误。

**项目含义：**

- Badcase 先判断是 Retrieval Error 还是 Generation Error。
- Retrieval 正确但 Answer 错误时，优化 Prompt / Context Assembly，而不是盲目调 Embedding。

## 8. RAG 为什么会幻觉？

常见原因包括：证据没有被召回、Context 被截断、Chunk 缺少关键条件、模型忽略指令、
Query 理解错误、引用粒度过粗，以及系统允许模型生成不存在的业务编号。

**项目含义：**

- 回答必须只引用实际返回的 Evidence。
- 无证据时应明确拒答。
- 保存 query、filters、各阶段 hits、最终 context 和 answer，支持 Badcase 归因。

## 9. 什么情况下不应该用 RAG？

当问题是纯结构化计算、强事务查询、精确状态变更，或答案必须来自权威实时系统时，
优先使用 SQL / API / 规则引擎。RAG 适合需要从非结构化或半结构化知识中找证据并组织答案的场景。

**项目含义：**

- “统计 P0 缺陷数量”应使用结构化查询。
- “支付失败历史上有哪些典型根因”适合 RAG。
- 混合问题应由 Retrieval Planner 组合工具，而不是让 LLM 猜数据库。

## 10. RAG 如何持续优化？

建立可重复的 Evaluation → Badcase → Root Cause → Fix → Regression 闭环。
优化必须定位到具体阶段，并用同一 Gold Set 对比修改前后指标和延迟。

**项目含义：**

- Chunk、Embedding、TopK、Filter、Rerank、Prompt 都是实验变量。
- 每次优化记录假设、变更、指标和结论。
- 没有评测收益的复杂度应删除，而不是因为“看起来更高级”而保留。

## 11. 当前待验证问题

- bge-m3 在中文测试用例和缺陷上的实际召回表现。
- 用例“一实体一 Chunk”与长文档标题切分的 Recall 差异。
- cosine distance 下 HNSW 参数对召回率和延迟的影响。
- Metadata Filter 放在向量检索前、中、后的效果差异。
- 首版 Evaluation Dataset 的 Gold Evidence 粒度。

