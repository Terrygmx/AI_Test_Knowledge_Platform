# Review：步骤 5 —— Phase 12 审阅辅助工具

- 日期：2026-10-24 17:30（Asia/Shanghai）
- 分支：`phase-12-evaluation-dataset`
- 范围：Phase 12 人工确认的 checklist 导出 / 进度查询 / 审阅回填

## 变更内容

- `app/evaluation/phase12_review.py`：
  - `export_review_checklist()`：逐条导出问题、意图、过滤条件、Gold 来源 / Chunk 证据原文（从语料取 chunk 正文）、答案要点，附空白 `review` 区（decision / review_notes / reviewed_by=Terry 预填）。
  - `apply_review_decisions()`：把 checklist 的审阅决定写回数据集；逐条构造 `Phase12Query` 复用全部契约校验（审阅人必须是 Terry、时区、备注必填），并重新执行语料证据校验；默认写到新文件，不覆盖候选原文件。
  - `review_progress()`：confirmed / rejected / pending 计数与数据集哈希。
- `scripts/phase12_review.py`：`export` / `progress` / `apply` 三个子命令；apply 支持 `--mode formal`（回填后必须全部 confirmed）。
- `tests/test_phase12_review.py`：6 个测试覆盖导出完整性、回填不覆盖源文件、非法 decision、缺备注、formal 半确认拒绝、进度计数。

## 验证

- SQLite 全量：722 passed, 17 skipped；`ruff`、`git diff --check` 通过。
- 实际运行：`export` 生成 100 条 checklist（含 chunk 正文）；`progress` 显示 100 pending。

## Review 发现与处置

1. 【已修】回填输出写到新路径后，`corpus_dataset` 相对路径无法从输出位置解析（语料在 eval/fixtures 旁）；这是加载器的既有行为，工具契约不变——测试显式传 `corpus` 验证回填文件，CLI 的 apply 输出路径仍建议放在同目录或使用绝对语料路径。已在 CLI 文档字符串中说明 formal 校验命令。
2. 【确认设计】工具不提供"批量确认"以外的快捷状态：每条必须带 review_notes（confirmed 说明核对依据，rejected 说明错误原因），与"证据粒度 = 关键字句、要点 = 一个事实"的已确认规则一致。
3. 【确认边界】checklist 里的 `chunk_content` 只是核对辅助；审阅对象仍是 dataset 中的 evidence 字符串。回填时重新执行语料校验，审阅中改动 Gold 会导致校验失败而不是静默通过。
4. 【遗留】CLI 暂无逐条交互式审阅模式（Terry 直接编辑 checklist JSON）；如需要可后续加 `interactive` 子命令。

## 结论

人工确认的工具链已就绪：Terry 只需编辑 checklist 的 decision / review_notes 两个字段，回填与校验全部自动化。可以进入下一项。
