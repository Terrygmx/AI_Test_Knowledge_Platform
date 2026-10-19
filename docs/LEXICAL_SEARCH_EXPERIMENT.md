# Phase 8：BM25 / Lexical Search 实验

- 日期：2026-10-20
- 分支：`phase-8-bm25`
- 检索实现：应用层倒排索引 + Okapi BM25
- 分词：Unicode 字母数字标识符完整保留；连续 CJK 字符 unigram + bigram
- 数据集：`eval/fixtures/phase8_simulated.json`
- 数据集 SHA-256：`da92624f5f287fcc83943fb68ebc6d969d6d71b35e5b519215c687606e996150`
- Gold Set：24 条 Query（18 可回答 / 6 不可回答）
- 原始 manifest：`results/lexical-phase8.json`
- BM25 参数：`k1=1.2`、`b=0.75`

## 1. 实验目标

Phase 8 要回答两个问题：

1. 当前 `%query%` 形式的 `ILIKE` 子串检索，在精确业务编号、版本号、优先级和
   多关键词 Query 上有哪些结构性失败？
2. 应用层分词 + BM25 能否在不引入 `zhparser` / `pg_jieba` 的前提下，覆盖中文
   关键词与精确标识符，并为 Phase 9 提供可解释的排序分数？

实验固定 Phase 6 的 `entity+heading` 检索单元，不调用 Embedding，也不访问模型
服务。这样指标变化只来自词法检索本身。

## 2. 数据集与标注

Phase 6 的 40 条 Query 主要描述业务场景，不足以单独验证“精确编号”检索。
Phase 8 因此新增一份独立 Gold Set，复用 Phase 6 的 12 条 TestCase、5 条 Defect、
5 条 Requirement 和 3 篇 Markdown 文档，并新增 1 条 Defect（`BUG-1234`），
用于一个明确的混合标识符案例：

```text
P0 BUG-1234 login-api v1.3
```

因此 Phase 8 共 6 条 Defect、47 个 Chunk。新增项不是无关数据：它正是组合关键词
坏例和负样本误召回分析的证据来源，同时会改变 BM25 的文档频率与 IDF，不能把
Phase 8 语料描述成与 Phase 6 完全相同。

24 条 Query 分为：

| 类别 | 数量 | 例子 |
|---|---:|---|
| `exact_identifier` | 3 | `SHOP-BUG-001`、`SHOP-PAYMENT-002 幂等键` |
| `exact_version` | 3 | `v2.3 login 账户锁定` |
| `exact_priority` | 3 | `P0 payment 重复扣款` |
| `semantic_paraphrase` | 9 | `退出登录只清掉浏览器 Cookie 够不够？` |
| `unanswerable` | 6 | `登录支持企业微信扫码吗？` |

标注仍使用 `source_type + source_id + 原文证据片段`，不绑定 Chunk ID。可回答
Query 共 26 条证据标注。

数据集中的业务内容为模拟数据，参考的公开 QA 示例仅用于字段组织，未复制正文。

## 3. 分词与索引

### 3.1 分词规则

| 输入片段 | 输出 token |
|---|---|
| `P0` | `p0` |
| `BUG-1234` | `bug-1234` |
| `login-api` | `login-api` |
| `v1.3` | `v1.3` |
| `café-déjà` | `café-déjà` |
| `支付超时` | `支`、`付`、`超`、`时`、`支付`、`付超`、`超时` |

- Unicode 先做 NFKC 规范化并转小写，因此全角编号和大小写差异不会破坏匹配；
- Unicode 标识符允许 `-`、`_`、`.` 连接，避免把版本号和模块名拆成无意义片段；
- 拉丁扩展字母（如 `café`）保留在 token 内；拉丁字母与 CJK 相邻时在 script
  边界切开，分别走标识符与中文 n-gram 路径；
- 中文 unigram 保留单字召回，bigram 提供更强的双字词语信号；
- 不删除停用词，尤其保留 `不`、`无` 等否定词，避免改变证据语义。

这不是语言模型分词，也不声称能正确识别所有中文词边界。当前目标是可解释、
跨 SQLite / PostgreSQL 一致、无需额外数据库扩展的基线。

### 3.2 倒排索引与 BM25

倒排表记录 `term -> {document_index: term_frequency}`，同时记录每篇文档长度和
词项文档频率。打分使用：

```text
idf(t) = log(1 + (N - df(t) + 0.5) / (df(t) + 0.5))
score(D, Q) = Σ idf(t) * tf * (k1 + 1)
              / (tf + k1 * (1 - b + b * |D| / avgdl))
```

查询中的多个词项会累加分数；`matched_terms` 与每个词项的贡献单独保留，便于
解释为什么命中。排序使用分数降序，再以 `source_type + source_id` 稳定打破平局。

`matched_terms` 是**分词后的词项**，不是已经归纳好的业务概念。中文 Query 的命中项
可能是单字和跨词 bigram，例如 `支付超时` 会得到 `支`、`付`、`超`、`时`、`支付`、
`付超`、`超时`。这能证明词项交集和分数来源，但不能单独证明语义相关性；Phase 9
若向用户展示解释，应优先提取原 Query 中的连续片段或可读词，而不是直接罗列全部
unigram / bigram。

### 3.3 为什么暂不选数据库扩展

- PostgreSQL 内置 `to_tsvector('simple')` 不会正确切分中文；
- `zhparser` / `pg_jieba` 需要额外安装扩展，当前 CI 与本地 SQLite 路径无法共用；
- 当前知识库规模很小，应用层实现先验证检索质量与接口契约，代价是每次查询
  需要读取候选 Chunk 并建内存索引；
- 数据量增大后，应把倒排表 / 搜索服务持久化，而不是把这个实现当作大库性能方案。

## 4. 结果

### 4.1 总体指标

| 检索方式 | Recall@5 | Recall@10 | MRR | Query P95 |
|---|---:|---:|---:|---:|
| `ILIKE` 整串子串 | 0.056 | 0.056 | 0.056 | <0.001s |
| BM25 | **0.944** | **0.972** | **0.792** | <0.001s |

上表对应默认 `--top-k 10`；K 值集合由 `top_k` 动态决定，不会产生截断后仍冒用
`recall@10` 的字段。`--top-k 1` 只输出 Recall@1，`--top-k 5` 只输出 Recall@5，
`--top-k 7` 输出 Recall@5 / Recall@7。manifest 同时记录 `top_k` 与 `k_values`。

BM25 在 Recall@5 的 26 条证据中命中 24 条，Recall@10 命中 25 条。`ILIKE`
只命中 1 条，因为大部分 Query 不是正文中的连续子串。

### 4.2 分类结果

| 类别 | ILIKE Recall@5 | BM25 Recall@5 | BM25 MRR |
|---|---:|---:|---:|
| `exact_identifier` | 0.333 | **1.000** | **0.833** |
| `exact_version` | 0.000 | **1.000** | 0.667 |
| `exact_priority` | 0.000 | 0.833 | 0.667 |
| `semantic_paraphrase` | 0.000 | **0.944** | **0.861** |
| `unanswerable` | — | — | — |

`exact_version` 的 Recall@5 为 1.0 但 MRR 只有 0.667：相关证据都能进入 Top-5，
但部分 Query 的首位结果不是目标证据。`exact_priority` 的 Recall@5 为 0.833，
说明仅靠 `P0` / `P1` 这类高频词仍不足以完全区分业务对象。

### 4.3 组合关键词基线与坏例

用当前 `ILIKE` 的实际语义检索：

```text
P0 BUG-1234 login-api v1.3
```

| 检索方式 | 命中数 | 首位来源 |
|---|---:|---|
| ILIKE | 0 | — |
| BM25 | 1 | `BUG-1234` |

这是最直接的结构性差异：`ILIKE` 把整串 Query 当成一个连续子串，要求正文中
按同样顺序出现全部内容；BM25 将其拆成 `p0`、`bug-1234`、`login-api`、`v1.3`
四个词项，再按词项重要性和词频打分。

### 4.4 不可回答 Query

6 条不可回答 Query 在 BM25 下全部返回了结果（误召回率 1.0），`ILIKE` 为 0.0。
这不代表 `ILIKE` 的拒答能力更好，而是它把大多数正常 Query 也一起漏掉了；
BM25 的“有词项交集”不等于“有足够证据回答”。Phase 9 不能把非空结果直接当作
可回答，仍需要融合、阈值或后续 Rerank。

## 5. 解释

1. **精确标识符更适合词法检索**：`BUG-1234`、`login-api`、`v1.3` 是离散符号，
   不是自然语言语义。Embedding 会压缩这些细节，词法索引则按原样保留。
2. **组合条件需要词项级匹配**：用户经常把编号、模块、版本和优先级一起输入。
   `ILIKE` 的整串匹配对这种 Query 没有召回；BM25 能对多个词项分别匹配并排序。
3. **中文 unigram + bigram 是一个可运行基线**：它不需要额外扩展，且能覆盖
   “支付”“超时”这类常见词；但它仍会把相似字面、不同语义的文本排在前面，
   不能替代向量语义召回。
4. **高频元数据词会稀释区分度**：`P0`、`login`、`v2.4` 出现在多个实体中，
   仅靠 BM25 无法保证目标证据排第一；Phase 9 可以结合 Metadata Filter、
   Vector 排名和 RRF / Weighted Score。
5. **小样本结果不能外推**：24 条 Query 足以验证接口和失败模式，但不足以证明
   BM25 在生产分布上的绝对水平。

## 6. 复现

```bash
# 只校验数据集与参数，不写文件、不访问网络
uv run python scripts/lexical_experiment.py --dry-run

# 生成 manifest 与指标
uv run python scripts/lexical_experiment.py \
  --output results/lexical-phase8.json
```

脚本完全离线，不需要 PostgreSQL、Docker、OMLX 或 API Key。manifest 记录
数据集 SHA-256、`git_commit` / `git_dirty`、分词规则、BM25 参数、逐类指标与
基线坏例。已提交的 manifest 必须来自 `git_dirty=false` 的提交；如果从脏工作区
生成，只能作为临时结果，不能作为长期引用的实验事实源。

生产侧可通过：

```python
KnowledgeChunkService.lexical_search(session, query="BUG-1234", limit=10)
```

返回 `(chunk, bm25_score, matched_terms)`。该接口当前不改动
`UnifiedSearchService` / `/search` 的无 score 响应契约；Phase 9 再决定对外融合
响应如何暴露分数。

## 7. 已知局限

- 只有 24 条 Phase 8 Query 和 47 个 Chunk，指标受小样本影响；
- 中文 unigram + bigram 不理解词性、实体边界和同义表达；
- 查询与文档使用同一种轻量规范化，没有 stemming / 拼音 / 错别字纠错；
- 每次查询全量读取 Chunk、经 ORM 转成 Python 对象并重建内存索引；
- 服务层只有 1,000 个合成 Chunk 的宽松耗时上限测试，没有 P50 / P95 端到端延迟，
  也没有 10k+ Chunk、并发和内存占用数据；该测试只守护明显退化，不是生产容量结论；
- `ILIKE` 基线是应用层等价实现，延迟不包含 SQL 全表扫描成本；
- 未评测 Precision@K、NDCG、Rerank 或生成答案质量；
- 负样本误召回率很高，当前没有可用于生产的“无证据”阈值。

## 8. 结论与下一步

Phase 8 的结论是：

- 对业务编号、模块名、版本号、优先级和组合关键词，BM25 明显优于整串
  `ILIKE`；应保留为 Hybrid Retrieval 的关键词通道；
- 应用层分词足以完成当前规模的验证，不需要在 Phase 8 强行引入
  `zhparser` / `pg_jieba`；
- BM25 仍会误召回不可回答问题，也会被高频元数据词干扰；
- Phase 9 应把 Vector Top-K 与 BM25 Top-K 分开评估，再比较 Weighted Score /
  RRF，而不是把两种不可直接比较的分数硬相加。
