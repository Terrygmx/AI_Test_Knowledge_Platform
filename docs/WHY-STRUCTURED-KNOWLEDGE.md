# 为什么原始实体与检索单元必须分离

## 一句话结论

TestCase、Defect、Requirement、Document 是事实来源；KnowledgeChunk 是可重建的检索表示。
如果把两者合成一张“大文本表”，会同时损失业务语义、引用稳定性和评测能力。

## 1. 两者回答的问题不同

原始实体回答：

- 这条用例的业务编号、优先级、步骤和预期结果是什么？
- 这个缺陷的根因、状态和关联用例是什么？
- 这条需求属于哪个模块、哪个版本？

检索单元回答：

- 这段文本是否与当前 Query 语义相关？
- 它应该进入哪个候选集、什么排序？
- 生成的回答可以引用哪一段证据？

同一实体可能需要多种 Chunk 方案、不同 Embedding 模型或不同版本索引，
但业务实体本身不应该因此被改写。

## 2. 引用稳定性

Citation 的锚点必须是业务编号（Case ID / Bug ID / Req ID / Doc ID），
而不是可重建的 Chunk 主键。重新切分、换模型或增量索引后，Chunk 可以变化，
但用户看到的来源编号必须保持稳定。

## 3. 结构化过滤

“登录模块 P0 的冒烟用例”需要 module / priority / case_type 等结构化列过滤，
而不是依赖向量模型从文本里“猜”。原始实体保留这些字段，Chunk 保存来源与 Metadata，
两者配合才能做 Retrieval Planner 和 Metadata Filter。

## 4. 评测与 Badcase 归因

如果只在 Chunk 上评测，很难判断失败来自：

- 原始实体本身缺失字段；
- Chunk 切分不合理；
- Embedding 语义不足；
- Metadata Filter 错误；
- Rerank 排序错误。

保留原始实体和 Chunk 的映射，才能把失败定位到正确阶段，并重跑相同 Gold Set。

## 5. 当前实现边界

- `knowledge_chunks.source_type + source_id` 是弱逻辑引用，不建外键。
- `content_hash` 支持后续增量索引判断内容是否变化。
- `embedding` 是派生数据，可以删除后重新生成。
- Phase 2 的索引入口按来源重建 Chunk，不修改原始实体。

## 6. 不应做的事

- 不把 Chunk 当作业务事实来源。
- 不在 Chunk 上修改 Case ID / Bug ID。
- 不把向量写入原始实体表。
- 不为 Chunk 建强外键，阻止来源删除或尚未录入。
