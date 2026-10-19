# Phase 5：数据接入

- 日期：2026-10-20
- 状态：完成
- 范围：Excel / CSV / Markdown / 普通文本 PDF

## 1. 入口

```bash
uv run python scripts/ingest.py table data/cases.csv
uv run python scripts/ingest.py table data/cases.csv --type testcase
uv run python scripts/ingest.py markdown data/spec.md --doc-id DOC-001
uv run python scripts/ingest.py pdf data/spec.pdf --doc-id DOC-002
```

## 2. Excel / CSV

### 表头

表头统一归一化为 snake_case：

- `Case ID` / `caseid` → `case_id`
- `Bug ID` → `bug_id`
- `Req ID` → `req_id`
- `Doc ID` → `doc_id`
- `Expected` → `expected_result`
- `Type` → `case_type`

不传 `--type` 时按业务编号列识别实体；`title + content` 识别为 Document。

### 清洗规则

- CSV 使用 UTF-8（兼容 BOM）；非 UTF-8 明确报错，不静默乱码；
- `None` / `NaN` / 空串 / `null` 视为空值；
- `tags` / `related_cases` 支持 `|`、逗号、中文逗号、分号分隔；
- 字符串去首尾空白；
- 同一文件内重复业务编号只保留第一条，后续行计入 `skipped`。

### 幂等与事务

- 已存在业务编号执行局部更新；
- 新业务编号创建；
- 整个文件一个事务，任意行校验失败则整批回滚；
- 错误信息包含 `row N` 和字段级原因。

### 限制

- 不自动把中文表头映射到 Schema；Phase 5 先要求标准字段名或英文别名；
- 不自动创建缺失的业务编号；
- Excel 多 Sheet 暂不批量导入，一次导入一个 Sheet / 文件。

### 表格导入 document 的正文

`documents` 表只存元数据，没有 `content` 列。因此表格里的 `content` 不会写进
`documents`，而是落成一条 `source_type=document` 的 KnowledgeChunk（正文即检索单元）。
按 `doc_id` 替换，重复导入不会产生重复 Chunk；`Type` 列在文档语义下归一为 `doc_type`
（在用例语义下是 `case_type`）。

> 这里有个已经踩过的坑：早期实现把 `content` 直接交给 `DocumentCreate`，
> 而 `ORMInput` 未设 `extra="forbid"`，Pydantic 会**静默忽略**多余字段，
> 结果正文无声丢失且测试全绿。现在正文显式走 Chunk，并有落库断言。

## 3. Markdown

流程：

```text
Markdown
→ front matter 元数据
→ Heading Tree
→ Sections
→ Document + KnowledgeChunk
```

- 每个标题一个 Section，对应一个 Document Chunk；
- 正文按原始行切片，表格、代码块、列表保持原文；
- `path` 保存标题层级，例如 `("支付", "失败重试")`；
- 重复导入同一 `doc_id` 会更新文档元数据并替换该文档旧 Chunk。

边界（Review 后补齐）：

- **无标题文档**：整篇作为一个 Section（`title=fallback_title`、`level=1`）。
  这是纯文本 PDF 抽取结果的常态；早期实现会产出 0 个 Chunk、正文整体丢失且无任何报错；
- **首个标题之前的“前言”**：保留为标题 `前言` 的独立 Section，不再被丢弃；
- 空标题（`## ` 之类）：正文并入上一个 Section（首个标题就为空时落到文档标题下），
  不再连带正文一起丢弃；
- 首个标题就为空且没有正文可并入时，Section 落到文档标题（`fallback_title`）下：
  正文优先于锚点，文档标题此时仍取自首个**非空**标题，两者不一致是刻意的；
- 空正文 Section（``# A`` 紧接 ``## B``）：正文用标题路径兜底（如 `A / B`），
  标题本身是有效检索锚点，且不会产生空向量（上游拒绝空白输入）。
- **空文档（0 Section）不清索引**：`ImportService` 保留该 `doc_id` 的旧 Document
  元数据与 Chunk 并记 warning，CLI 打印「没有解析出任何正文」。否则导入一个空文件
  就等于静默清空索引。

当前不做固定长度切分、Semantic Chunking；这些在 Phase 6 对比实验。

## 4. PDF

第一版只调用 `pypdf` 抽取普通文本层：

- 不做 OCR；
- 不还原复杂表格 / 多栏版面；
- 无文本层 PDF 明确报错；
- 抽取后按 Markdown 标题规则切 Section；无标题时整篇作为一个 Section。

## 5. 与后续阶段边界

- Phase 5 只负责解析、规范化、去重和保存 Document / Chunk；
- 不在导入时调用 Embedding，避免大批量导入与模型服务耦合；
- 导入后的 document Chunk 通过 `scripts/index_entities.py --only document` **补向量**
  （增量：只处理 `embedding IS NULL` 的 Chunk，不重建正文）；
  `--only testcase,defect,requirement` 仍是「按实体重建」语义，两者不要混淆；
- 补向量 / 重算按 `EMBEDDING_BATCH_SIZE` 用主键游标分批读取与提交，
  上游中途失败时已完成批次保留，失败批次回滚；
- **`--only document --rebuild`**：强制重算 document 的全部向量（含已有向量的 Chunk）。
  切分策略（Phase 6）或 Embedding 模型变更后必须走这个开关，增量补向量不会刷新旧向量；
- 重复导入文档会清掉已有向量（新 Chunk 的 `embedding` 为空），
  `MarkdownImportResult.indexed_chunks_replaced` 会带回被清掉的条数，CLI 会打印提示；
- Metadata Filter、Hybrid Search、Rerank 仍属于 Phase 8–11。

## 6. 验收测试

- `tests/test_ingestion.py`：CSV / Excel / Markdown / PDF / 事务回滚 / 重复导入；
- 无标题文档与无标题 PDF 各产出 1 个 Chunk；前言保留；重复导入返回
  `indexed_chunks_replaced`；document Chunk 可通过 `backfill_embeddings` 补向量；
- 空文档保留已有 Chunk（`indexed_chunks_replaced=0`）；
- `tests/test_ingest_script.py`：CLI 参数、错误退出与提示分支
  （`document_chunks` / 已向量化替换 / 空文档不清索引）；
- `tests/services/test_knowledge_chunk_service.py`：实体到 Chunk 映射。
