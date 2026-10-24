# Review：Phase 4 知识模型收口 + Phase 5 数据接入

- 分支：`phase-3-llm`
- 评审时间：2026-10-20 01:40（Asia/Shanghai）
- 评审范围：上次 review（`review/review-phase-3-llm-20261020-0102.md`，基线 `e68f79f`）之后的新增提交
  - `97325cd` fix(review)：修正健康探测与 RAG 契约边界
  - `061db7d` feat(model)：完成 Phase 4 知识模型收口
  - `aea36af` feat(ingestion)：完成 Phase 5 数据接入
- 变更规模：`git diff --stat e68f79f..aea36af` → 33 files changed, 1505 insertions(+), 72 deletions(-)
- 复验结果：
  - SQLite 默认：`286 passed, 6 skipped`
  - 真实 PostgreSQL + pgvector（`aitkp_test`）：`291 passed, 1 skipped`
  - `uv run ruff check .` 全绿；`uv run alembic check` 无漂移
  - 手工验证：无标题文档导入 → `section_count=0`、0 Chunk（H3 证据）；RAG 超时 `rag_request_timeout=1` + 3s 慢调用 → 503 在 **3.04s** 才返回（H1 证据）
- 工作区：无未提交业务改动（仅 `review/` 下未跟踪的历史 review 文件）

---

## 1. 总体结论

这次增量把两个完整阶段（Phase 4 知识模型、Phase 5 数据接入）落到了代码 + 文档 + 测试三件套上，
并且把上一次 review 的高优先级问题基本真正闭环了：

- `/health` 的 `llm` / `embedding` 已改为「打 `/models` 并核对模型名」的真实探测，不再是 Key 非空即通过；
- `evidence_sufficient` 与 `citation_valid` 解耦，无引用回答不再原样返回，而是降级为 `UNCITED_ANSWER` 拒答文案；
- `PROJECT_SCOPE.md` 的 Phase 状态、README「当前状态」、handover 的阶段说明都已同步；
- Phase 4 的字段契约（TestCase / Defect / Requirement / KnowledgeChunk）有专门的 `tests/test_phase4_contract.py` 收口；
- Phase 5 的 CSV / Excel / Markdown / PDF 四条解析链路都有测试，事务回滚与重复导入也覆盖了。

但新增阶段也带来了三个高优先级缺口，且都属于「文档承诺与实现不一致」或「保护机制实际不生效」这类会误导后续判断的问题：

1. `rag_request_timeout` 只是「提前返回 503」，并不能真正限制响应时间，`with ThreadPoolExecutor` 的 `shutdown(wait=True)` 仍会等工作线程跑完；
2. Phase 5 产出的 `document` Chunk 没有任何向量化入口，`scripts/index_entities.py` 只认 testcase/defect/requirement，而 `docs/PHASE5_INGESTION.md` §5 明确写「可通过 `index_entities.py` 统一生成向量」；
3. 无 Markdown 标题的文档（典型的纯文本 PDF 抽取结果）会生成 **0 个 Chunk**，正文整体丢失，而 PDF 小节同样承诺「无标题时整篇作为一个 Section」。

---

## 2. 阻断性问题

无。

---

## 3. 高优先级发现

### 3.1 `rag_request_timeout` 不限制响应时间，超时保护实际不生效

- 位置：`app/api/rag.py:34-51`、`app/core/config.py:59-62`
- 现象：`with ThreadPoolExecutor(max_workers=1) as executor:` 在 `future.result(timeout=...)` 抛 `TimeoutError` 后，`with` 退出会调用 `shutdown(wait=True)`，必须等工作线程执行完才返回。
- 实测证据：`rag_request_timeout=1.0` + 3 秒慢调用 → 返回 503，但耗时 **3.04s**（日志 `POST /api/v1/rag/answer -> 503 3042.2ms`），中间件把 3 秒完整算进请求耗时；上一轮同样场景（5s 慢调用）在 5.0s 返回。
- 次生问题：`llm_timeout=120` > `rag_request_timeout=30`，底层 OpenAI 客户端最长可占用 120s，30s 预算无法中断；真实 OMLX（`Ling-3.0-tiny-oQ6e`，11–60s）仍会占满 worker 并阻塞请求线程。
- 影响：OpenAPI 描述「超过 `RAG_REQUEST_TIMEOUT` 会返回 503」与 README「30 秒预算」的表述会让人误以为有硬超时；实际只是「到点先给一个 503，连接仍被占用」。
- 建议：
  1. 改为模块级共享 executor（或 `# noqa` + `executor.shutdown(wait=False)`），让超时真正释放请求；
  2. 约束 `llm_timeout <= rag_request_timeout`（或在配置层校验并明确报错）；
  3. 若无法真正中断，就如实改 README / OpenAPI / `docs/handover.md` 的措辞（「到点返回 503，底层调用仍会继续，需异步化解决」），并记入「已知取舍」。

### 3.2 Phase 5 的 `document` Chunk 没有向量化入口，与文档矛盾

- 位置：`scripts/index_entities.py:49-59`（`_parse_only` 的 `allowed = {"testcase", "defect", "requirement"}`）、`scripts/index_entities.py:60-74`（`_collect` 只收 TestCase / Defect / Requirement）
- 矛盾点：`docs/PHASE5_INGESTION.md:84` 写「导入后的 Chunk 可通过 `scripts/index_entities.py` 统一生成向量」，但该脚本不认 `document`，`--only document` 会直接报「未知实体类型」。
- 后果：`ImportService` 产出的 `source_type=document` Chunk 的 `embedding` 恒为 `None`（实测确认），永远进不了向量检索；`/health.metrics.chunks_without_embedding` 只增不减且没有补救入口，正好落在上一轮刚建立的可观测性盲区里。
- 建议：二选一并保持一致——① `_collect` 增加 `document` 分支（把 Document 对应 Chunk 重新生成/补向量，注意复用已有 chunk 内容而不是重建实体）；② 修正文档，并新增 `scripts/index_documents.py` 或在 `index_entities.py` 加显式 `document` 支持。无论哪种都要补一条「document chunk 补向量」的测试。

### 3.3 无标题文档生成 0 个 Chunk，正文整体丢失（PDF 场景最致命）

- 位置：`app/ingestion/markdown.py:52-84`（`sections` 只在 `headings` 非空时构建）、`app/ingestion/service.py:114-136`（先删旧 chunk 再 `add_all([])`）
- 现象：`MarkdownParser.parse` 仅在存在 `heading_open` token 时才生成 section；`headings` 为空则 `sections=[]`，且没有「整篇作为一个 Section」的回退。
- 实测证据：`ImportService.import_markdown(doc_id="DOC-NOTITLE", source="纯文本第一段。\n第二段内容。\n")` → `MarkdownImportResult(section_count=0, created=True)`，Document 的 `meta={'section_count': 0}`、`content_hash=e3b0c442...`（空串 sha256），`KnowledgeChunk` 查询结果为 **0 条**。对照 `# 标题\n正文内容\n` → `section_count=1`、1 条 Chunk。
- 矛盾点：`docs/PHASE5_INGESTION.md:78` 明确承诺「无标题时整篇作为一个 Section」。
- 影响：PDF 第一版只做纯文本抽取（不做版面还原），绝大多数真实 PDF 抽取结果没有 Markdown 标题 → 导入「成功」但无任何 Chunk，Phase 5 的 PDF 能力实际不可用，且不会有任何报错。
- 建议：`headings` 为空时回退为单个 Section（`title=fallback_title`、`level=1`、`content=body.strip()`），并补「无标题文档 → 1 个 Chunk」与「无标题 PDF」两条测试。同时考虑 `headings` 非空但首个标题之前有前言文本的情况（当前前言同样被丢弃）。

---

## 4. 中优先级发现

### 4.1 重复导入会静默清空已向量化的 document Chunk

- 位置：`app/ingestion/service.py:113-136`
- `_save_document` 每次都 `delete(KnowledgeChunk where source_type=document and source_id=doc_id)` 再 `add_all(新 chunk)`，新 chunk 的 `embedding` 为 `None`。
- 一旦 3.2 的补向量入口打通，重复导入同一 `doc_id` 就会把已有向量静默清掉，把文档重新「打回」检索不可见状态。
- 建议：删除前先检查是否已存在带向量的 chunk，若有则打印/返回警告（或在 `MarkdownImportResult` 里带回 `reembedded=False` 之类的提示）。

### 4.2 `/models` 探测对非标准 OpenAI-compatible 服务会误报 false；`rerank` 仍只按 Key 判断

- 位置：`app/api/health.py:57`、`app/api/health.py:130-146`
- 上一轮已把 `llm` / `embedding` 升级为真实 `/models` 探测，这很好；但 `_model_available` 依赖 `GET /models` 且要求响应 `data[].id` 精确等于配置模型名。部分 OpenAI-compatible 服务不实现该端点或返回别名（如带后缀），会把「可用」误报为不可用。
- 同时 `capabilities.rerank` 仍是 `bool(settings.rerank_api_key)`，与 llm/embedding 的真实探测口径不一致。
- 建议：至少在 README / handover「已知取舍」里标注 `/models` 探测的适用前提，并在探测失败时记 warning 日志（现在 `except Exception` 直接吞掉）；rerank 在 Phase 11 接入时改用同一套探测。

### 4.3 `TableImporter` 用 `title + content` 推断 document，但 Document 没有 `content` 字段

- 位置：`app/ingestion/table.py:120-121`；`app/schemas/document.py:23-49`（`DocumentCreate` 无 `content`）
- 推断逻辑认为含 `title` + `content` 列的表格是文档，实际写入时 `content` 列因 `ORMInput` 未 `extra="forbid"` 而被静默忽略（pydantic 默认忽略多余字段）。
- 后果：用 CSV 批量导入文档时，正文被无声丢弃，只剩标题与元数据；`infer_entity_type` 的单测（`tests/test_ingestion.py:42`）只断言了类型推断，没有断言落库结果，所以测试是绿的。
- 建议：要么给 Document 加 `content` 字段（或改为走 Chunk 落正文），要么移除 `title+content → document` 的推断并明确报错；并补一条「CSV 导入 document 后正文不丢」的断言式测试。

### 4.4 `rag_request_timeout` / `llm_timeout` 不匹配 + 每请求新建线程池

- 位置：`app/core/config.py:59-62`、`app/api/rag.py:34`
- 30s 预算 > 底层 120s 超时，两者关系颠倒；`ThreadPoolExecutor` 每请求新建（额外开销，且无法共享并发上限）。详见 §3.1。
- 建议：统一为 `llm_timeout <= rag_request_timeout`，executor 提到模块级并设合理 `max_workers`。

---

## 5. 低优先级 / 一致性观察

- **L1 `scripts/embedding_probe.py --pairs` 仍是空操作**（`:38-42`）：只把 help 改成「默认行为，保留用于兼容旧命令」，参数仍被解析后丢弃。上一轮建议是删除或实现分支，这次只改了文案；建议直接删掉该参数或让它显式输出「--pairs 已废弃」。
- **L2 `MarkdownParser` 用 `if section.title` 过滤空标题 section**（`app/ingestion/markdown.py:84`）：这是有意的（`## ` 空标题不入库），但没有注释说明；与 §3.3 的无标题回退叠加时更容易读错。建议加一行注释。
- **L3 `scripts/ingest.py` 未提示「Chunk 尚无向量」**：导入文档后只打印 `sections=N`（`:74-77`），用户不知道这些 Chunk 还不能参与检索。建议追加一行提示（配合 §3.2 一起改）。
- **L4 README「当前状态」未列 Phase 5 CLI**：`README.md:188-215` 的清单里还没有 `scripts/ingest.py`（handover 与 `docs/PHASE5_INGESTION.md` 都有），且 Phase 5 未在「当前状态」首段点名（首段仍写到 Phase 4 为止）。建议在清单里补 Phase 5 条目。
- **L5 测试未覆盖「无标题」与「document 补向量」**：`tests/test_ingestion.py` 的 Markdown 用例都是有标题的两段式（`:158-190`），因此 §3.3 的问题没有被红灯拦住。

---

## 6. 上次 review 问题闭环情况

| 上次问题 | 状态 | 依据 |
|---|---|---|
| §3.1 `/health` 对 openai_compatible 只校验 Key | 已修复 | `app/api/health.py:130-146` 新增 `_model_available`（打 `/models` 并核对模型 id），`_check_llm` / `_check_embedding` 均改走真实探测；`tests/api/test_health.py` 补 dead endpoint / missing model 用例 |
| §3.2 `evidence_sufficient` 语义不符 | 已修复 | 语义与引用解耦（阈值是否通过），docstring 同步更新；已手工复验 `max_distance` 配置/未配置两种行为 |
| §3.3 慢接口无超时/降级边界 | 部分修复 | 新增 `RAG_REQUEST_TIMEOUT=30`（`config.py:62`）、`ThreadPoolExecutor` + 503 `llm_error`、OpenAPI 标注实验性慢接口；但超时并不真正生效（本次 §3.1） |
| §4.1 文档残留 Ollama / Key 表述 | 已修复 | README / handover / PROJECT_SCOPE 已统一为 OMLX |
| §4.2 `embedding` 能力判断与真实可用性脱节 | 已修复 | 与 llm 共用 `_model_available`，实测 dead endpoint / 模型名错误均返回 false |
| §4.3 `embedding_probe.py --pairs` 空操作 | 部分修复 | 仅更新 help 文案，参数仍无效（本次 L1） |
| §4.4 `citation_valid=false` 仍返回 LLM 原文 | 已修复 | 无引用回答返回拒答文案（`UNCITED_ANSWER`，「证据不足：模型回答未通过引用校验。」） |
| §4.5 PROJECT_SCOPE Phase 状态不一致 | 已修复 | `docs/PROJECT_SCOPE.md:130-136`：Phase 2/3 = 代码完成 + 链路已验证（质量待评测），Phase 4/5 = 完成 |
| §5 低优先级（snippet 截断、CLI 打印 rank/distance、retriever Literal、测试 `_env_file=None`） | 已修复 | `MAX_SNIPPET_CHARS=500`；`scripts/ask.py` 输出 rank/distance；`retriever` 收敛；`tests/test_embedding.py` 用 `_env_file=None` |

---

## 7. 纪律符合性（本次增量）

| 纪律 | 结论 | 依据 |
|---|---|---|
| TDD 先红后绿 | 符合 | Phase 4 配 `tests/test_phase4_contract.py`（76 行），Phase 5 配 `tests/test_ingestion.py`（211 行）+ `tests/test_ingest_script.py`；实现与测试同提交 |
| 测试不依赖 Docker / 外部 API | 符合 | 解析/导入链路全部离网；PG 相关仍可跳过，SQLite 默认 286 passed |
| PG 专属能力有实机验证 | 符合 | PG `291 passed, 1 skipped`；`alembic check` 无漂移 |
| Evidence First | 部分 | 无引用拒答、distance 暴露都已到位；但 document 类 Chunk 无向量即不可检索却无补救入口（§3.2），导入成功却 0 Chunk 也不报错（§3.3） |
| LLM 可替换 | 符合 | `LLMClient` Protocol 未变更，`llm_provider` 仍可切换 |
| 不用 LangChain / LlamaIndex | 符合 | `app/ingestion` 全部手写，`pyproject.toml` 仅新增 `markdown-it-py` / `pypdf` / `pandas` / `openpyxl` |
| 统一错误信封 | 符合 | RAG 超时走 `llm_error` + 503；导入失败（CSV 非 UTF-8、无文本层 PDF、未知类型）均转 `ValueError` 并由 CLI 明确退出 |
| 约定式提交 + CHANGELOG | 符合 | `feat(model):` / `feat(ingestion):` / `fix(review):`；CHANGELOG 有 Phase 4 / Phase 5 条目 |
| 欠账显式记录 | 部分 | README「已知取舍」表、handover 待办齐全；但 Phase 5 的「不生成向量」未写入 README 取舍表，文档 §5 反而给了错误入口（§3.2）；慢接口超时的真实边界未如实记录（§3.1） |
| 决策原因写进 docstring | 符合 | `app/ingestion/pdf.py`、`markdown.py`、`service.py`、`scripts/ingest.py` 均有取舍说明；`docs/PHASE5_INGESTION.md` 明确写了不做什么与留给哪个阶段 |

---

## 8. 建议的下一步（按优先级）

1. 修 §3.3：无标题文档/纯文本 PDF 回退为单个 Section，并补两条红灯测试（这是唯一会导致「导入成功但数据为零」的问题）。
2. 修 §3.1：让 `rag_request_timeout` 真正生效（模块级 executor / `wait=False`），并统一 `llm_timeout <= rag_request_timeout`；同步校正 README / OpenAPI 表述。
3. 修 §3.2：给 document Chunk 打通补向量入口（扩展 `index_entities.py` 或新增脚本），并同步 `docs/PHASE5_INGESTION.md` §5。
4. 修 §4.3：处理 CSV 导入 document 时 `content` 被静默丢弃的问题（加字段或移除推断），补落库断言。
5. 修 §4.1 + L3：重复导入时提示/保护已有向量，CLI 明确告知「Chunk 尚未生成向量」。
6. 低优先级：L1 删除 `--pairs`；L2 补注释；L4 README 补 Phase 5 条目与 CLI。
