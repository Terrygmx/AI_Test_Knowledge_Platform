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
- 导入后的 Chunk 可通过 `scripts/index_entities.py` 统一生成向量；
- Metadata Filter、Hybrid Search、Rerank 仍属于 Phase 8–11。

## 6. 验收测试

- `tests/test_ingestion.py`：CSV / Excel / Markdown / PDF / 事务回滚 / 重复导入；
- `tests/test_ingest_script.py`：CLI 参数与错误退出；
- `tests/services/test_knowledge_chunk_service.py`：实体到 Chunk 映射。
