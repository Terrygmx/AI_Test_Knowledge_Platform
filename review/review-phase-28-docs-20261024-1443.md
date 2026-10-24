# Phase 28 Review：项目文档

- 日期：2026-10-24 14:43（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 结论：**架构总览已补齐；其余计划文档已由各阶段文档覆盖，未重复造册**

## 已实现

- 新增 `docs/ARCHITECTURE.md`：单篇回答“请求经过哪些组件、边界是什么”，
  覆盖请求路径、数据流、评测体系索引与五条硬边界。
- 计划中的其他文档映射到既有文件，避免双份维护：
  - DATA_MODEL → `docs/PHASE4_KNOWLEDGE_MODEL.md`
  - RETRIEVAL → Phase 6–11 各实验报告
  - EVALUATION → `docs/RETRIEVAL_EVALUATION.md` + `docs/GENERATION_EVALUATION.md`
  - BADCASE → `app/evaluation/badcase.py`（Phase 19 review）
  - PERFORMANCE → `app/evaluation/performance.py`（Phase 20 review）
  - CHANGELOG → `CHANGELOG.md`

## 校验

- 全量 SQLite：685 passed, 17 skipped；
- 文档中引用的路径全部存在于当前工作区。

## 边界

- BADCASE / PERFORMANCE 暂以代码 docstring + review 记录承载，
  待真实压测与 Badcase 持久化后再独立成篇；
- ARCHITECTURE 中明确 Pipeline 未替换公开 API 默认路径，
  接入 HTTP 前不得宣称在线链路已升级。
