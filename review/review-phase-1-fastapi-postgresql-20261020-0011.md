# Review：Phase 2 修复 + Phase 3 最小 RAG（增量为 `53c88b2` / `5e6de7f` / `5b949c7`）

- 分支：`phase-1-fastapi-postgresql`
- 评审时间：2026-10-20 00:11（Asia/Shanghai）
- 评审范围：上次 review（`97dc343`）之后的新增提交
  - `53c88b2` fix(review)：修复 Phase 2 契约与入口问题
  - `5e6de7f` docs(review)：记录 Phase 2 评审结果
  - `5b949c7` feat(rag)：Phase 3 最小 RAG 与 Citation
- 复验结果：SQLite 默认 `245 passed, 6 skipped`；真实 PostgreSQL 17.11 + pgvector 0.8.7（`aitkp_test`）`250 passed, 1 skipped`；`ruff check` 全绿；`alembic check` 无漂移；开发库 6 张表 + HNSW 索引 + `vector`/`pg_trgm` 扩展均已就位
- 未提交改动：无（工作区干净）

---

## 1. 总体结论

上次 review 提的 4 个高优先级问题已全部闭环（409 契约、索引入口、文档数字、`/health` 的 `llm` 恒真），且都配了回归测试，这一点做得扎实。Phase 3 的最小 RAG 也确实按「手写、可替换、Evidence First」的纪律落地：引用编号越界直接报错、无证据不构造 LLM、证据与问题分区、字符预算。

但 Phase 3 引入了**新的、性质更严重的风险**：`grounded=true` 目前只表示「引用编号合法」，并不表示「引用与结论相关」。实测中让 LLM 用 `[2]` 回答一个只与 `[1]` 相关的问题，系统仍返回 `grounded=true` 且引用 `[2]`；检索也没有任何相关性阈值，任意不相关问题都会取 TopK 送进 Prompt 并被判定为有据。这直接关系方案 §13 的 Citation Accuracy ≥ 90% / Faithfulness ≥ 90%，也关系 `PROJECT_SCOPE.md` §5「无法找到足够证据时必须明确回答证据不足」。建议在进入 Phase 8–11 前先把 `grounded` 的语义校正或改名为更诚实的表述。

按严重程度排序的发现见 §2–§5。

---

## 2. 阻断性问题

无。当前实现可运行、测试全绿、契约一致。

---

## 3. 高优先级发现

### 3.1 `grounded=true` 语义过强：只校验编号存在，不校验引用与结论的相关性

- 位置：`app/generation/rag.py:66`、`app/generation/rag.py:108`
- 现象：`extract_citations()` 只检查编号落在 `[1, len(available)]` 且非空，就置 `grounded=True`。实测：
  - 证据 `[1]=登录失败`、`[2]=支付失败`，LLM 回答「登录失败检查缓存。[2]」→ 返回 `grounded=True`、`citations=[B/支付失败]`；
  - 用完全无关的问题检索到 3 条 chunk（cosine distance 0.96–1.03，即**几乎完全不相关**），LLM 回答「答案[1][2][3]」→ 仍返回 `grounded=True`。
- 影响：`grounded` 是 RAG 对外最重要的信任信号。当前语义下它等价于「LLM 遵守了编号格式」，而不是「回答有据可依」；后续 Phase 14（Faithfulness）与 Phase 17（Citation）若基于 `grounded` 统计 Citation Accuracy，会得到虚高的、不可信的结论，属于典型的「用格式合规冒充证据正确」。
- 建议（三选一，按成本递增）：
  1. 把字段名改为 `citation_valid`（或 `has_citations`），并在文档/README/CHANGELOG 明确「不代表 Faithfulness」；
  2. 在 `RAGResponse` 中把 `grounded` 拆成 `citation_valid` + 独立的 `faithfulness` 判定（Phase 14 引入）；
  3. 立即加一道最小校验：回答必须引用**排序靠前的证据**（如至少一个编号 ≤ `min(top_k, 2)`），并把 distance 传入用于拒答（见 §4.1）。

### 3.2 检索没有相关性下限，TopK 结果无条件送进 Prompt

- 位置：`app/generation/rag.py:96`、`app/services/knowledge_chunk_service.py:118`
- 现象：只要向量检索返回非空，`RAGService.answer()` 就把全部结果当证据；`distance` 只被 `assemble_context` 丢弃（`for chunk, _distance in results`）。实测用「数据库迁移怎么回滚？」检索仅含「登录成功」的知识库，`top_k=5` 仍返回 `retrieved_count=1`、`grounded=True`。
- 影响：`PROJECT_SCOPE.md` §5 要求「无法找到足够证据时，系统必须明确回答证据不足」，当前只有「检索结果为空」才算证据不足，语义上等于把「最不相关的一条」也当作足够证据。README「已知取舍」已写「相关性阈值需真实评测集校准，暂不伪造阈值」——这个取舍本身是对的，但**当前实现连阈值的接口都没有**，Phase 13/14 无法直接对比「有阈值 / 无阈值」的效果。
- 建议：把 `distance` 一路传到 `RAGResponse`（例如 `citations[i].distance` 或独立的 `evidence` 列表），并加一个可配置的 `max_distance`（默认关闭/宽松）作为实验开关；在此之前至少把 distance 记进日志，让 Badcase 归因能定位到 Retrieval 而非 Generation。

### 3.3 `/health` 的 `llm` 探测结果被永久缓存，且只捕获 `httpx.HTTPError`

- 位置：`app/api/health.py:78`
- 现象：`_llm_cache` 是进程级全局变量，一旦取到 `True`/`False` 就再也不刷新。实测：首次探测成功后再把 `httpx.get` 换成必失败的实现，`_check_llm()` 仍返回 `True`。另外 `except httpx.HTTPError` 只覆盖 httpx 异常，实测把 `httpx.get` 换成抛 `TimeoutError` 时，异常会冒泡出 `_check_llm()`（在 `/health` 里会变成 500）。
- 影响：`/health` 是能力自检的入口，永久缓存会让「Ollama 后来 pull 了模型 / 后来挂了」两种变化都不可见，与该文件「提前暴露」的设计意图相悖；非 httpx 异常冒泡则与 `PROJECT_SCOPE.md` 对可观测性的要求相反。
- 建议：改为带 TTL 的缓存（如 60s）或每次请求探测（Ollama 本地探测成本很低，`timeout=1.5`）；并把 `except` 放宽到 `Exception`，失败即 `False`。

### 3.4 无 Embedding 的 Chunk 会被 RAG 静默忽略，索引状态不可观测

- 位置：`app/api/chunks.py:21`、`app/services/knowledge_chunk_service.py:118`
- 现象：`POST /api/v1/chunks` 明确「不生成 Embedding」，写出的 chunk `embedding IS NULL`。pgvector 的 `ORDER BY embedding <=> ...` 对 `NULL` 列不会返回这些行，实测混存「有向量」与「无向量」两条 chunk 时，检索只返回有向量的那条，且不报错。
- 影响：索引半完成（例如 `index_entities.py` 中途失败，或手工 POST 了 chunk）时，检索会**静默少召回**，且没有任何地方提示「有多少 chunk 缺向量」。这正是 `LEARNING_QUESTIONS.md` 里典型的 Ingestion 类 Badcase，如果没有可观测手段，Phase 19 的归因会非常困难。
- 建议：加一个索引状态查询（`GET /api/v1/chunks?has_embedding=false` 或 `/health` 里加 `chunks_without_embedding` 计数），并在检索日志里记录「候选总数 / 有向量数」。

---

## 4. 中优先级发现

### 4.1 `distance` 在 Pipeline 中被丢弃，无法支撑后续评测与 Badcase

- 位置：`app/generation/context.py:45`、`app/schemas/rag.py:36`
- 现象：检索算出了 cosine distance（唯一真实的打分依据），但 `Citation` 只有 `source_type / source_id / chunk_index / snippet`，distance 既不出 API 也不进日志。
- 影响：`PROJECT_SCOPE.md` §7 要求 Badcase 能定位到 Retrieval / Rerank 阶段；没有 distance 就无法区分「检索没召回」和「召回了但排序靠后」。另外实现计划 Phase 17 的 Evidence 结构明确要求 `score / rank / retriever`，当前 `Citation` 与之不对齐，Phase 17 需要返工。
- 建议：`Citation` 增加可选 `score` / `distance` / `rank` / `retriever` 字段（对齐 Phase 17 的 Evidence 定义），并在 RAG 链路打一条结构化日志（question / top_k / hits / distances）。

### 4.2 上下文预算在单块超限时失效

- 位置：`app/generation/context.py:56`
- 现象：`if citations and used + len(block) > max_context_chars: break` 对**第一块**无条件放行。实测单块 1900 字符、`max_context_chars=1` 时，仍产出 1933 字符的上下文并生成 1 条引用。
- 影响：预算的意图（避免 Prompt 溢出 / 成本失控）在第一块就失效；`MAX_CHUNK_CHARS=2000` 与 `MAX_CONTEXT_CHARS=8000` 的组合下，4 个满长 chunk 就能突破总预算（每块加来源头后 >2000）。真正的 LLM 上下文限制由 `llm_max_tokens` 兜底，但 Prompt 会被截断得不可预测。
- 建议：改成「超限即截断本块到剩余预算」或「第一块也参与预算判断（允许至少 1 块但截断到预算）」，并补一个 `max_context_chars` 小于单块长度的测试固定行为。

### 4.3 引用解析规则过窄，正常表述会被误判为「缺少引用」

- 位置：`app/generation/rag.py:22`
- 现象：`_CITATION_PATTERN = r"\[(\d+)\]"` 不接受空格与多编号连写。实测 `[1,2]` 与 `[1 ]` 都会抛 `LLMError("LLM 回答缺少证据引用")`（并映射成 503），而 `[01]` 被解析为 `[1]`。
- 影响：本地小模型（qwen3:8b）输出格式不稳定，`[1, 2]` 是很常见的写法；当前会把「格式不合规」当成「LLM 故障」返回 503，而不是重试或归一化为「无据」。这会让 Phase 3 的真实链路验证（handover §5 待办第 2 条）以难以理解的方式失败。
- 建议：放宽为 `\[\s*(\d+(?:\s*[,，]\s*\d+)*)\s*\]`，并区分「编号越界」（确实是 LLM 造号，应报错）与「无编号」（可归一化为 `grounded=false` 而不是 503）。

### 4.4 Phase 3 声称完成，但全部真实链路验证仍未执行

- 位置：`README.md:188`、`docs/PROJECT_SCOPE.md:134`、`docs/handover.md:131`
- 现象：`EMBEDDING_API_KEY` 仍为空，Ollama 实测 `GET /api/tags` 返回 `{"models":[]}`（本机无模型）。因此 `scripts/ask.py` 与 `POST /api/v1/rag/answer` 的真实路径从未端到端跑过；PG 侧的验证用的是 `FakeEmbeddingClient` + FakeLLM。
- 影响：与上次 review §3.4 是同一类问题——「链路通」与「质量验证」是两件事。当前 Phase 2/3 都被标为「核心完成」，但方案 §13 的全部指标（Recall@5 / MRR / Citation Accuracy / Faithfulness / P95）一个都没被触及。README 已诚实地把「真实 bge-m3 语义质量实验」列为未完成，这一点是好的；但 `PROJECT_SCOPE.md` 的 Phase 3 写「核心完成」容易让接手者以为只剩边角工作。
- 建议：把 Phase 状态细分为「代码完成 / 真实验证完成」两列，或在这两行补 `（真实链路未验证）` 后缀。

---

## 5. 低优先级 / 一致性观察

- **`/api/v1/search` 的 `status` 仍是裸 `str`**：`app/api/search.py:38` 用 `_normalize_status()` 手写成集合比对，而 `severity` / `priority` / `type` 已统一用 `enum_query()`。结果是同一份接口里 `status` 在 OpenAPI 里没有枚举文档（实测 schema 为 `string`），且未知值返回 400（`domain_error`）而其他枚举返回 422——**两套不一致的错误码**。建议统一用 `enum_query` 并让未知值走 422，或明确说明 400 的理由。
- **`severity` 过滤对 document 的排除行为是对的**：实测 `?source_type=document&severity=major` 返回 `total=0`（document 无该列，整实体排除），符合已有的「不满足过滤条件的实体必须排除」设计。但未补测试固定该行为。
- **Chunk 列表 `GET /api/v1/chunks` 无排序保证之外的过滤**：当前只支持 `source_type` / `source_id`，无法按「缺向量」排查（见 §3.4）。
- **`scripts/index_entities.py` 只用 `testcase/defect/requirement`，文档内容未接入**：脚本 docstring 已说明「Phase 5 接入 Parser/Chunker 后再补」，与计划一致；但 CHANGELOG 的 Phase 2「索引入口」条目未提此限制，建议同步。
- **`enum_query` 与 `PriorityField` 语义重复**：`app/schemas/common.py:158` 新增的 `enum_query()` 与既有 `PriorityField` 等实现完全相同（同一 `Annotated` 构造）。方向上「收敛到一处」是对的，但目前存在两个入口，建议后续把字段级别名也改为 `enum_query` 的统一实现。
- **CI 只有 SQLite 路径**：`.github/workflows/ci.yml` 跑 `ruff` + `pytest`（SQLite），PG 路径仍靠人工。这是上次建议的第 6 条，已部分落实（CI 建起来了），PG 门禁缺失属于已知取舍，建议至少在 README 注明。
- **测试命名 `test_postgres_returns_grounded_answer`**（`tests/api/test_rag_api.py:38`）疑似编辑器自动替换产物，语义不明，建议改成 `test_rag_answer_returns_grounded_answer`。

---

## 6. 上次 review 问题的闭环情况

| 上次问题 | 状态 | 依据 |
|---|---|---|
| §3.1 chunk 唯一冲突返回 500 | 已修复 | `KnowledgeChunkService._commit()` 捕获 `IntegrityError` → `DuplicateError(409)`；`tests/api/test_chunks.py:38` 断言 409 且 `error.code == "duplicate"` |
| §3.2 Phase 2 无索引入口 | 已修复 | `scripts/index_entities.py`（`--dry-run` / `--only` / 先 Embedding 再删旧索引）；`tests/test_index_script.py` 覆盖 dry-run 与「上游失败保留旧索引」 |
| §3.3 文档数字脱节 | 已修复 | README/CHANGELOG/handover 更新为 245/250，并加「以本地实际输出为准」 |
| §3.4 Phase 2 验证边界未闭环 | 部分修复 | 新增 `scripts/embedding_probe.py` 与 README 待办项；但 Key 仍为空，探针未实际运行 |
| §4.1 `/health` 的 `llm` 恒 true | 已修复（但有新缺陷） | `_check_llm()` 实际探测 Ollama `/api/tags`；但引入永久缓存与窄异常捕获（§3.3） |
| §4.2 不支持 `severity` | 已修复 | `app/api/search.py:34` 加入 `severity`，`tests/api/test_openapi.py` 固定 OpenAPI 契约 |
| §4.3 查询参数枚举不归一化 | 已修复 | 新增 `enum_query()`，`tests/api/test_search.py` 断言 `p0` / `FIXED` / `Smoke` 均 200 |
| §4.4 `retrieval_*` 配置无人消费 | 已标注 | `app/core/config.py:60` 注释为 Phase 9/11；README 已知取舍表新增一行 |
| §4.5 依赖超前 / 无 CI | 部分修复 | 新增 `.github/workflows/ci.yml`（SQLite + ruff）；依赖仍超前（可接受） |
| §5 HNSW 漂移检查靠字符串 | 已修复 | 迁移里加 `# hnsw: idx_knowledge_chunks_embedding_hnsw` 标记 + `tests/test_migrations.py::test_hnsw_index_has_stable_marker` |
| §5 `docker-compose` 注释误导 | 已修复 | 注释改为「统一 UTF-8 / C locale；中文全文检索方案在 Phase 8 再定」 |
| §5 空包 `__init__.py` 无说明 | 已修复 | `app/generation/__init__.py` 等补充了 Phase 归属说明 |
| §5 `SourceType` 重复定义 | 已修复 | 收敛到 `app/schemas/common.py:33`，另两处改为 import |
| §7 Phase 4 文档欠账 | 已修复 | 新增 `docs/WHY-STRUCTURED-KNOWLEDGE.md` |

---

## 7. 纪律符合性（针对本次增量）

| 纪律 | 结论 | 依据 |
|---|---|---|
| TDD 先红后绿 | 符合 | 新增 4 个测试文件 + 4 个测试修改，与实现同提交；`5b949c7` 中 `tests/test_rag.py`(206 行) 与 `app/generation/*`(283 行) 同步 |
| 测试不依赖 Docker / 外部 API | 符合 | RAG 核心逻辑用 `FakeLLM` + `FakeEmbeddingClient` 在 SQLite 上验证；PG 专属用例 `pytest.skip`（6 处） |
| PG 专属能力有实机验证 | 符合 | `tests/test_retrieval_pg.py` 新增 2 个 RAG 用例；本次 PG 复验 250 passed |
| Evidence First | 部分 | 引用编号越界/缺失 fail fast 做得好；但 `grounded` 语义过强、distance 被丢弃（§3.1/§3.2/§4.1） |
| 不用 LangChain / LlamaIndex | 符合 | `app/generation/` 全部手写，`pyproject.toml` 无相关依赖 |
| LLM 可替换 | 符合 | `LLMClient` Protocol + OpenAI-compatible 实现；`get_llm_client()` 工厂；Ollama / 云 API 双通道 |
| 统一错误信封 | 符合 | `LLMError` → 503，`DomainError` → 400，测试已固定 |
| 约定式提交 + CHANGELOG | 符合 | `fix(review):` / `docs(review):` / `feat(rag):` 三段式；CHANGELOG 新增 Phase 2 修复项与 Phase 3 条目 |
| 欠账显式记录 | 符合 | README 已知取舍新增 4 行（检索参数未消费、Fake 语义、Phase 3 仅 Vector、拒答阈值待校准） |
| 决策原因写进 docstring | 符合 | `context.py` / `rag.py` / `llm.py` / `index_entities.py` / `embedding_probe.py` 均有取舍说明 |

---

## 8. 建议的下一步（按优先级）

1. 校正 `grounded` 语义（§3.1）：改名或拆分，避免用「编号合法」冒充「有据」——这是当前最可能污染后续所有评测结论的问题。
2. 把 `distance` 传进 `Citation` 并加相关性阈值开关（§3.2 / §4.1），同时给 RAG 链路补结构化日志。
3. 修 `/health` 的 LLM 探测缓存与异常捕获（§3.3），并加「缺向量 chunk 数」的可观测指标（§3.4）。
4. 放宽引用解析规则并区分「越界」与「无引用」的错误语义（§4.3），为真实 Ollama 输出做准备。
5. 统一 `search.status` 的枚举处理与错误码（§5），修 `test_postgres_returns_grounded_answer` 命名。
6. 配置 `EMBEDDING_API_KEY` + `ollama pull qwen3:8b`，跑通 `scripts/embedding_probe.py` 与 `scripts/ask.py`（§4.4），并把结果写进 `docs/`——在此之前，Phase 2/3 的「完成」应理解为「代码完成」。
