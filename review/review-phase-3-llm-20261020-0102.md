# Review：Phase 3 语义校正、本机 OMLX 接入与真实链路验证

- 分支：`phase-3-llm`
- 评审时间：2026-10-20 01:02（Asia/Shanghai）
- 评审范围：上次 review 基线 `5b949c7` 之后的新增提交
  - `a2eb6ec` fix(rag)：校正 `grounded` 语义并补齐检索可观测性
  - `2e78037` feat(embedding)：默认 Embedding 切换到本机 OMLX
  - `e68f79f` feat(rag)：LLM 切换到本机 OMLX 并验证真实链路
- 复验结果：
  - SQLite 默认：`261 passed, 6 skipped`
  - 真实 PostgreSQL 17.11 + pgvector 0.8.7（`aitkp_test`）：`266 passed, 1 skipped`
  - `ruff check` 全绿；`alembic check` 无漂移
  - 真实 OMLX 链路已复跑：Embedding 探针、登录/支付检索、无关问题拒答、`max_distance=0.2` 拒答均与 `docs/RAG_EXPERIMENT.md` 一致
- 工作区：无未提交改动

---

## 1. 总体结论

这次增量把上次 review 的 4 个高优先级问题真正闭环了，而且不是靠改文档糊过去：

- `grounded` 已拆成 `citation_valid` + `evidence_sufficient`，`citation_valid` 的 docstring 明确「不代表 Faithfulness」；
- `Citation` 补了 `rank` / `distance` / `retriever`，RAG 链路也打了检索距离日志；
- 上下文预算首块也受约束，`[1, 2]` / `[1 ]` 引用格式已支持；
- `/health` 的 LLM 探测改为 60s TTL 并捕获非 httpx 异常；
- 缺向量 Chunk 可通过 `GET /chunks?has_embedding=false` 与 `/health.metrics.chunks_without_embedding` 排查；
- 真实 OMLX bge-m3 的相似度、索引、pgvector 检索与 LLM 端到端链路都已实际跑通，并写进 `docs/EMBEDDING_EXPERIMENT.md` / `docs/RAG_EXPERIMENT.md`。

这是从「代码完成」跨到「真实链路验证完成」的一步，值得肯定。

但新增的 OMLX 默认配置引入了两个新的中高优先级问题：`/health` 对 `openai_compatible` 通道只判断 Key 非空，导致服务挂了、模型名写错也会报 `llm=true`；`evidence_sufficient` 在「阈值已配置、但 LLM 没有输出引用」时返回 `None`，语义与 docstring 不一致。另有若干文档残留旧 Ollama / Key 表述。详见下文。

---

## 2. 阻断性问题

无。

---

## 3. 高优先级发现

### 3.1 `/health` 对 `openai_compatible` 通道只校验 Key，不校验服务与模型

- 位置：`app/api/health.py:97-100`
- 现象：`_check_llm()` 在 `llm_provider == "openai_compatible"` 时只做 `ready = bool(settings.llm_api_key)`。实测把 `llm_base_url` 改成 `http://127.0.0.1:9999/v1`、`llm_model` 改成 `nope` 后，`/health.capabilities.llm` 仍返回 `true`。
- 影响：默认配置已经切到 OMLX，这是当前唯一的默认 LLM 通道；此时 `/health` 的 `llm=true` 完全不反映 OMLX 是否启动、模型是否加载。`docs/RAG_EXPERIMENT.md` 第 1 节把 `llm=true` 当作「真实链路可用」的证据，但该字段本身无法支撑这个结论。这与 `app/api/health.py` 文件头「提前暴露比等到调用时才报错更友好」的设计意图直接冲突。
- 建议：
  - 对 `openai_compatible` 也发起轻量探测：`GET {base_url}/models`（OMLX/Ollama 都支持），确认 HTTP 200 且 `llm_model` 在返回列表中；
  - 或者至少做一次 `chat.completions` 最小调用（`max_tokens=1`），但成本更高；
  - 补一条测试：dead endpoint + 正确 Key → `llm=false`；endpoint 存活但模型名不存在 → `llm=false`。
  - 顺带把 `embedding` capability 也改为真实探测；当前 `bool(key) or local_url` 对「本地地址但服务没起」同样返回 `true`（实测 `http://127.0.0.1:9999/v1` → `embedding=true`）。

### 3.2 `evidence_sufficient` 在「已配置阈值但 LLM 无引用」时返回 `None`，与字段语义不符

- 位置：`app/generation/rag.py:103-106`、`app/schemas/rag.py:51-53`
- 现象：`evidence_sufficient = True if max_distance is not None and bool(citations) else None`。实测 `max_distance=0.5`、证据 distance=0.1、LLM 回答「回答无引用」时，返回 `citation_valid=false, evidence_sufficient=None`。
- 影响：`schemas/rag.py` 的 docstring 写的是「`evidence_sufficient` 在配置 `max_distance` 时表示证据是否通过阈值；未配置时为 `None`」。当前实现把「配置了阈值但回答没引用」也归为 `None`，调用方无法区分「没做相关性判定」和「做了判定但回答不可追溯」。对自动评测尤其危险：统计 `evidence_sufficient is True` 会漏掉这类失败，统计 `is False` 也会漏掉。
- 建议：把 `evidence_sufficient` 与 `citation_valid` 解耦：
  - 若 `max_distance is not None`，只要存在通过阈值的证据就返回 `True`，否则 `False`；这与 LLM 是否引用无关；
  - `citation_valid` 单独表示「回答引用了合法编号」；
  - 如果确实想表达「回答层面的证据充分性」，就新增 `answer_supported` 字段，不要复用 `evidence_sufficient`。
  - 补测试覆盖 `max_distance` + 无引用、`max_distance` + 越界引用、`max_distance=None` + 无引用三种组合。

### 3.3 默认 LLM 是 11–60 秒级模型，但 API 没有超时/降级边界

- 位置：`app/core/config.py:56-60`、`app/api/rag.py:18-29`、`docs/RAG_EXPERIMENT.md:73-77`
- 现象：`llm_timeout=120`，实测单次真实 RAG 请求 11–60 秒（本次复跑登录问题约 64 秒，含检索与推理）。`POST /api/v1/rag/answer` 是同步 `def` 路由，直接阻塞 worker；文档已记录该延迟，但没有超时、取消、异步任务或降级策略。
- 影响：
  - 方案 §13 的 P95 目标是 `< 5s`，当前真实链路约 11–60 秒，差一个数量级；
  - 同步路由 + 120s 超时会让少量并发请求占满 worker（默认 uvicorn 线程池有限），影响其他 API；
  - 这不是「Phase 3 必须解决」，但默认配置已经把它变成主路径，handover 里只写「需评估」不足以防止误用。
- 建议：
  - 至少在 README/OpenAPI 明确标注 `/rag/answer` 是实验性慢接口，不建议作为生产默认；
  - 加一个更短的 `rag_request_timeout`（如 30s）并在超时后返回明确的 `llm_error`；
  - 或把 RAG 路由改为异步 + 线程池执行，避免阻塞事件循环/请求线程；
  - 在 Phase 14/20 性能评测前，不要把该接口宣传为可用于交互式场景。

---

## 4. 中优先级发现

### 4.1 文档中仍残留旧 Ollama / Key 表述，与默认 OMLX 不一致

- 位置：
  - `scripts/ask.py:8`：「需要 … `EMBEDDING_API_KEY` 与可用的 LLM（默认本地 Ollama）」
  - `scripts/embedding_probe.py:5`：「需要配置 `EMBEDDING_API_KEY`」
  - `README.md:29`：技术栈表仍写「LLM：本地 Ollama / 云 API」，未提 OMLX
  - `README.md:96`：「未配置 Key 时，涉及 Embedding / Rerank / LLM 的代码路径不可运行」——本机 OMLX 现在允许空 Key
  - `docs/PROJECT_SCOPE.md:139`：「Embedding API Key 配置后，必须用 bge-m3 …」——当前默认 OMLX 无 Key 也可用
- 影响：新接手者按这些说明准备环境会走错路径（去 pull Ollama 或以为必须申请云端 Key）；handover 已更新，但 README/脚本文档没有完全同步，属于「文档与代码双轨」的残留。
- 建议：统一改成「默认本机 OMLX，无需 Key；远端服务才需要 Key」，并在 README 技术栈表补 OMLX；`ask.py` / `embedding_probe.py` 的 docstring 同步。

### 4.2 `/health` 的 `embedding` 能力判断与真实可用性脱节

- 位置：`app/api/health.py:55-56`
- 现象：`embedding` 只判断 `bool(key) or local_url`。实测 `http://127.0.0.1:9999/v1` + 空 Key → `embedding=true`；`http://127.0.0.1:28080/v1` + 不存在的模型名 → `embedding=true`。
- 影响：与 §3.1 同源，但影响的是 Embedding 链路。`docs/RAG_EXPERIMENT.md` 用 `embedding=true` 作为链路可用证据，当前该字段无法证明模型存在。
- 建议：复用 §3.1 的探测逻辑，检查 `/models` 中是否存在 `embedding_model`；本地地址不再等同于可用。

### 4.3 `scripts/embedding_probe.py` 的 `--pairs` 参数是空操作

- 位置：`scripts/embedding_probe.py:38-41`
- 现象：`parser.add_argument("--pairs", action="store_true", ...)` 解析后从未使用；不带参数与带 `--pairs` 行为完全相同（实测两者都执行内置 `PAIRS`）。
- 影响：低风险，但会让使用者误以为有别的模式；`--pairs` 是文档里的推荐命令，参数却无意义。
- 建议：要么删除该参数并让脚本默认跑内置 pairs，要么补 `--texts` / `--file` 等真实分支。

### 4.4 `citation_valid=False` 时仍返回 LLM 原始回答

- 位置：`app/generation/rag.py:103-109`
- 现象：LLM 输出「回答无引用」时，`RAGResponse.answer` 原样返回该文本，`citation_valid=false`，但 HTTP 200。实测 `answer="回答无引用"`、`citations=[]`。
- 影响：调用方如果只看 `answer`（很多 Agent/前端会这样），会把无来源内容当成可用回答；Evidence First 的边界在 API 契约上没有被强制执行。当前 `citation_valid` 是显式字段，文档也说明了，但「默认安全」不足。
- 建议：至少把 `answer` 置为「证据不足」或加前缀「未通过引用校验：」；或者把 `citation_valid=false` 且回答非空的情况定义为 422/409，避免下游误用。

### 4.5 `docs/PROJECT_SCOPE.md` 的 Phase 状态与 README/handover 不一致

- 位置：`docs/PROJECT_SCOPE.md:133-134`
- 现象：仍写「Phase 2 代码完成（真实语义未验证）」「Phase 3 代码完成（真实链路未验证）」，但 README 与 handover 已写「真实链路已验证」并附实验报告。
- 影响：范围文档是项目边界的权威来源，状态落后会误导后续 Phase 判断。
- 建议：更新为「代码完成 + 链路已验证；语义/评测未验证」，与 README/handover 对齐。

---

## 5. 低优先级 / 一致性观察

- **`retriever` 字段恒为 `"vector"`**：当前 Phase 3 只有向量检索，合理；但 `Citation.retriever` 是字符串而非枚举，Phase 8/9 引入 keyword/hybrid 后容易写脏值。建议现在收敛为 `Literal["vector", "keyword", "hybrid", "rerank"]`。
- **`distance` 只在 Citation 上暴露，`RAGResponse` 没有检索层统计**：`retrieved_count` 是过滤后的数量，原始命中数只在日志里。Phase 19 Badcase 归因需要「原始 hits / 过滤后 hits / top1 distance」，建议后续加结构化字段或 trace id。
- **`assemble_context` 的 snippet 仍保留完整原文**：`snippet=raw_content` 不截断，实测 5000 字符 chunk 的 context 只有 2033 字符，但 Citation.snippet 仍是 5000 字符，响应体可能比 Prompt 大很多。建议 snippet 也用 `_truncate` 后的内容（或单独限制 `MAX_SNIPPET_CHARS`）。
- **`scripts/ask.py` 不打印 `rank` / `distance`**：API 已返回，但 CLI 只打印 source_type/source_id/chunk_index；排查检索排序时还得去日志里找。建议补上 `rank` 与 `distance`。
- **`README` 的「CI 范围」已写 SQLite + ruff，但 handover 未提**：属于文档一致性小问题，建议同步。
- **`tests/test_llm.py` 的默认配置测试使用 `Settings(_env_file=None)`**：这是对的（避免受本地 `.env` 影响），但 `tests/test_embedding.py::test_default_config_points_to_local_omlx` 用的是 `Settings()`，会读取仓库 `.env`。当前 `.env` 恰好与默认值一致所以通过；若开发者本地 `.env` 指向云 API，该测试会失败。建议统一用 `_env_file=None`。
- **`app/api/health.py` 的注释「本地 Ollama 不需要 Key」已过时**：默认已是 OMLX，注释应改为「本机 OpenAI-compatible 服务不需要 Key」。

---

## 6. 上次 review 问题闭环情况

| 上次问题 | 状态 | 依据 |
|---|---|---|
| §3.1 `grounded` 语义过强 | 已修复 | 拆成 `citation_valid` / `evidence_sufficient`；`schemas/rag.py` 明确 `citation_valid` 不代表 Faithfulness；测试覆盖 `citation_valid=false` 与 `evidence_sufficient` |
| §3.2 检索无相关性下限、distance 丢失 | 已修复 | 新增 `max_distance`；`Citation.rank/distance/retriever`；RAG 与检索层均记录 distances；测试覆盖阈值拒答与部分保留 |
| §3.3 `/health` LLM 永久缓存 / 窄异常 | 部分修复 | 改为 60s TTL、`except Exception`；但 `openai_compatible` 分支仍只检查 Key（§3.1） |
| §3.4 无 Embedding Chunk 静默忽略 | 已修复 | `search()` 加 `embedding IS NOT NULL`；`GET /chunks?has_embedding=false`；`/health.metrics.chunks_without_embedding`；测试覆盖 |
| §4.1 distance 未进 API / 日志 | 已修复 | Citation 带 `rank/distance/retriever`；`RAGService.answer` 与 `KnowledgeChunkService.search` 都打日志 |
| §4.2 上下文预算首块失效 | 已修复 | `if used + len(block) > max_context_chars: break` 不再对首块放行；测试 `test_first_block_also_respects_budget` |
| §4.3 引用解析过窄 | 已修复 | 支持 `[1, 2]` / `[1 ]`；无引用不再 503，而是 `citation_valid=false` |
| §4.4 Phase 2/3 真实链路未验证 | 已修复 | OMLX Embedding/LLM 实测；`docs/EMBEDDING_EXPERIMENT.md` / `docs/RAG_EXPERIMENT.md` 记录结果与边界 |
| §5 `search.status` 裸 str | 已修复 | 新增 `SearchStatusQuery`；OpenAPI 暴露 `DefectStatus` / `RequirementStatus`；未知值 422；测试覆盖 |
| §5 `test_postgres_...` 命名 | 已修复 | 改名为 `test_rag_answer_returns_grounded_answer` |
| §5 CI 缺失 | 已修复 | `.github/workflows/ci.yml` 跑 ruff + SQLite pytest；PG 仍手工（README 已标注） |

---

## 7. 纪律符合性（本次增量）

| 纪律 | 结论 | 依据 |
|---|---|---|
| TDD 先红后绿 | 符合 | `a2eb6ec` 的 121 行 RAG 测试与实现同提交；`2e78037` / `e68f79f` 均补了对应测试 |
| 测试不依赖 Docker / 外部 API | 符合 | RAG/Embedding 单测仍用 Fake；真实 OMLX 验证只在手工实验与文档中执行，不进入默认测试 |
| PG 专属能力有实机验证 | 符合 | PG `266 passed`；HNSW 索引、pgvector 检索、真实 RAG 链路均复验 |
| Evidence First | 部分 | `citation_valid` 语义已校正、distance 已暴露；但无引用回答仍原样返回（§4.4），`evidence_sufficient` 组合语义有缺口（§3.2） |
| LLM 可替换 | 符合 | `LLMClient` Protocol + OMLX/Ollama/云 API 共用 OpenAI-compatible 客户端 |
| 不用 LangChain / LlamaIndex | 符合 | 无相关依赖；`app/generation` 全部手写 |
| 统一错误信封 | 符合 | Embedding 失败 503、LLM 失败 503、检索方言错误 400、未知 status 422 均走统一信封 |
| 约定式提交 + CHANGELOG | 符合 | `fix(rag):` / `feat(embedding):` / `feat(rag):`；CHANGELOG 有对应条目 |
| 欠账显式记录 | 部分 | README「已知取舍」已补 CI、Citation 信号、Chunk 索引状态；但 PROJECT_SCOPE 的 Phase 状态未同步（§4.5），脚本 docstring 仍有旧 Ollama 表述（§4.1） |
| 决策原因写进 docstring | 符合 | `context.py`、`rag.py`、`health.py`、`embedding.py`、实验报告均有取舍说明 |

---

## 8. 建议的下一步（按优先级）

1. 修 `/health` 的 `openai_compatible` / `embedding` 探测（§3.1 / §4.2）：检查 `/models` 并确认配置模型存在；补 dead endpoint 与 missing model 测试。
2. 校正 `evidence_sufficient` 的组合语义（§3.2），明确「阈值通过」与「回答有引用」是两个独立信号。
3. 给 `/rag/answer` 加超时/异步边界，并在 README/OpenAPI 标注实验性慢接口（§3.3）。
4. 统一文档中的 OMLX / Key 表述，更新 `PROJECT_SCOPE.md` Phase 状态（§4.1 / §4.5）。
5. 处理 `citation_valid=false` 时仍返回原文的问题（§4.4），至少加显著标记或拒答。
6. 低优先级：snippet 截断、CLI 打印 rank/distance、`retriever` 收敛为 Literal、测试统一 `_env_file=None`。
