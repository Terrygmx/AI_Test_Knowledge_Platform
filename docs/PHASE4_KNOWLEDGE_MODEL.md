# Phase 4 收口：测试知识模型

- 日期：2026-10-20
- 状态：完成
- 范围：TestCase、Defect、KnowledgeChunk 的字段契约与检索表示边界

## 1. 收口结论

Phase 1/2 已提前实现 TestCase、Defect 与 KnowledgeChunk 的主体结构。本轮没有
重复建表，而是完成契约验收并补齐两个实际缺口：

1. Chunk Metadata 丢失部分结构化过滤字段；
2. 缺陷 `related_cases` 只去重，没有校验业务编号格式。

## 2. TestCase 契约

事实来源表：`test_cases`。

| 字段 | 用途 |
|---|---|
| `case_id` | 稳定业务编号，Citation 锚点 |
| `title` | 用例标题与检索正文 |
| `module` / `sub_module` | 模块与子模块过滤 |
| `version` | 版本过滤 |
| `priority` | P0–P3 |
| `case_type` | smoke / functional / regression 等 |
| `precondition` / `steps` / `expected_result` | 检索正文 |
| `tags` | 标签过滤与检索辅助 |
| `source` | 来源系统 / 文件 |

Schema 负责业务编号格式、标题非空、受控词表归一化、标签去空白与去重。

## 3. Defect 契约

事实来源表：`defects`。

| 字段 | 用途 |
|---|---|
| `bug_id` | 稳定业务编号，Citation 锚点 |
| `title` | 缺陷标题与检索正文 |
| `module` / `version` | 模块与版本过滤 |
| `severity` / `priority` / `status` | 缺陷严重程度、优先级与状态 |
| `reproduce_steps` / `root_cause` / `resolution` | 检索正文 |
| `related_cases` | 与用例的弱关联，不建外键 |
| `source` | 来源系统 / 文件 |

`related_cases` 现在同时满足：

- 去首尾空白、去空值、保序去重；
- 每个编号必须符合与 Case ID 相同的业务编号规则；
- 不要求被关联用例已存在，保留“先录缺陷、后补用例”的能力。

## 4. KnowledgeChunk 契约

`knowledge_chunks` 是可重建的检索表示，不是事实来源。

保存内容：

- `source_type` + `source_id` + `chunk_index`：定位来源；
- `content`：面向检索的文本表示；
- `metadata`：用于后续 Metadata Filter 的结构化字段；
- `embedding`：可删除后重新生成的派生数据；
- `content_hash`：支持后续增量索引；
- `version`：来源版本。

### TestCase → Chunk Metadata

```json
{
  "module": "login",
  "sub_module": "sms",
  "version": "v1.3",
  "priority": "P0",
  "case_type": "smoke",
  "tags": ["冒烟", "登录"]
}
```

### Defect → Chunk Metadata

```json
{
  "module": "payment",
  "version": "v1.3",
  "priority": "P0",
  "severity": "critical",
  "status": "fixed",
  "related_cases": ["PAY-001", "PAY-002"]
}
```

Metadata 只放稳定、可过滤的结构化字段；完整正文仍以原始实体为准。

## 5. 边界与取舍

- Citation 始终锚定业务编号，不锚定 Chunk 主键。
- Chunk 不建指向业务实体的强外键，避免来源删除或延迟录入时阻塞索引。
- 当前一条 TestCase / Defect 对应一个 Chunk；多 Chunk 与切分实验在 Phase 6。
- Metadata 当前进入 Chunk 存储，真正参与检索过滤在 Phase 10。
- Phase 4 只验证模型与映射契约，不评价 Embedding 召回质量。

## 6. 验收测试

- `tests/test_models.py`：表、列、索引、唯一约束与可空性；
- `tests/test_schemas.py`：业务编号、受控词表、关联用例校验；
- `tests/services/test_knowledge_chunk_service.py`：实体到 Chunk 映射；
- `tests/test_phase4_contract.py`：Phase 4 完整 Metadata 契约与正文边界；
- `tests/test_migrations.py`：ORM 与 Alembic Schema 无漂移。
