-- ============================================================================
-- AI Test Knowledge Platform —— 初始 Schema（初稿）
--
-- 执行时机：docker compose up 首次启动时，由 docker-entrypoint-initdb.d 自动执行。
-- 注意：本文件是「初稿」。方案 Phase 1 要求 Schema 由你本人定稿，
--       字段命名、优先级取值、version 语义等请评审后再固化。
--
-- ⚠️ 中文全文检索提醒（重要）：
--   PostgreSQL 内置的 to_tsvector('simple'|'english') 对中文无效（按空格切分，
--   中文整句会被当成一个 token）。MVP 阶段有三条路：
--     (a) 装 zhparser / pg_jieba 扩展，建中文分词配置（效果最好，需自定义镜像）；
--     (b) 用 pg_trgm 做三元组模糊匹配（已启用扩展，零成本，召回一般）；
--     (c) 应用层分词后写入 tsvector 列（灵活，但需自己维护分词器）。
--   本文件先按 (c) 的思路保留 tsv 为普通列，由应用层写入，暂不建生成列，
--   以免后续换分词方案时必须 ALTER 生成列。
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;      -- pgvector
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- 中文模糊匹配兜底

-- ============================================================================
-- 1. 业务实体层：原始事实来源（Source of Truth）
-- ============================================================================

-- ---------- 测试用例 ----------
CREATE TABLE IF NOT EXISTS test_cases (
    id               BIGSERIAL   PRIMARY KEY,
    case_id          TEXT        NOT NULL UNIQUE,          -- 业务编号，如 LOGIN-001
    title            TEXT        NOT NULL,
    module           TEXT,
    sub_module       TEXT,
    version          TEXT,
    priority         TEXT,                                 -- P0 / P1 / P2 / P3
    case_type        TEXT,                                 -- smoke / functional / regression
    precondition     TEXT,
    steps            TEXT,
    expected_result  TEXT,
    tags             TEXT[]      NOT NULL DEFAULT '{}',
    source           TEXT,                                 -- 来源文件 / 系统
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_test_cases_filter
    ON test_cases (module, priority, version);
CREATE INDEX IF NOT EXISTS idx_test_cases_tags
    ON test_cases USING gin (tags);

-- ---------- 缺陷 ----------
CREATE TABLE IF NOT EXISTS defects (
    id               BIGSERIAL   PRIMARY KEY,
    bug_id           TEXT        NOT NULL UNIQUE,          -- 业务编号，如 BUG-1021
    title            TEXT        NOT NULL,
    module           TEXT,
    version          TEXT,
    severity         TEXT,
    priority         TEXT,
    status           TEXT,
    reproduce_steps  TEXT,
    root_cause       TEXT,
    resolution       TEXT,
    related_cases    TEXT[]      NOT NULL DEFAULT '{}',    -- 关联用例编号
    source           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_defects_filter
    ON defects (module, severity, status, version);

-- ---------- 需求 / 产品文档元数据 ----------
CREATE TABLE IF NOT EXISTS requirements (
    id            BIGSERIAL   PRIMARY KEY,
    req_id        TEXT        NOT NULL UNIQUE,
    title         TEXT        NOT NULL,
    module        TEXT,
    version       TEXT,
    priority      TEXT,
    status        TEXT,
    content       TEXT,
    source        TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_requirements_filter
    ON requirements (module, version);

-- ---------- 文档（Markdown / PDF 等非结构化来源）----------
CREATE TABLE IF NOT EXISTS documents (
    id            BIGSERIAL   PRIMARY KEY,
    doc_id        TEXT        NOT NULL UNIQUE,
    title         TEXT,
    doc_type      TEXT,                                    -- markdown / pdf / spec ...
    path          TEXT,                                    -- 原始文件路径
    content_hash  TEXT,                                    -- 增量索引判重用
    version       TEXT,
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ============================================================================
-- 2. 检索层：KnowledgeChunk（原始实体 ≠ 检索单元）
-- ============================================================================

CREATE TABLE IF NOT EXISTS knowledge_chunks (
    id            BIGSERIAL   PRIMARY KEY,
    chunk_uid     TEXT        NOT NULL UNIQUE,             -- source_type:source_id:chunk_index:hash
    source_type   TEXT        NOT NULL,                    -- testcase | defect | requirement | document
    source_id     TEXT        NOT NULL,                    -- 指向业务实体编号
    document_id   BIGINT      REFERENCES documents (id) ON DELETE CASCADE,
    chunk_index   INTEGER     NOT NULL DEFAULT 0,
    content       TEXT        NOT NULL,                    -- 检索表示（供 Embedding / BM25）
    metadata      JSONB       NOT NULL DEFAULT '{}'::jsonb, -- module / priority / version / tags ...
    content_hash  TEXT        NOT NULL,                    -- 内容指纹，增量更新用
    version       TEXT,
    embedding     VECTOR(1024),                            -- bge-m3 = 1024 维，换模型需同步改
    tsv           TSVECTOR,                                -- 由应用层写入（见文件头说明）
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 向量索引：HNSW + 余弦距离（配合 bge 系列归一化向量）
CREATE INDEX IF NOT EXISTS idx_chunks_embedding_hnsw
    ON knowledge_chunks USING hnsw (embedding vector_cosine_ops)
    WITH (m = 16, ef_construction = 64);

-- 关键词索引
CREATE INDEX IF NOT EXISTS idx_chunks_tsv
    ON knowledge_chunks USING gin (tsv);

-- 元数据过滤 + 中文模糊兜底
CREATE INDEX IF NOT EXISTS idx_chunks_metadata
    ON knowledge_chunks USING gin (metadata jsonb_path_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_content_trgm
    ON knowledge_chunks USING gin (content gin_trgm_ops);
CREATE INDEX IF NOT EXISTS idx_chunks_source
    ON knowledge_chunks (source_type, source_id);
CREATE INDEX IF NOT EXISTS idx_chunks_version
    ON knowledge_chunks (version);
CREATE INDEX IF NOT EXISTS idx_chunks_hash
    ON knowledge_chunks (content_hash);

-- ============================================================================
-- 3. 评测层
-- ============================================================================

-- ---------- 评测数据集（约 100 条人工确认 Query）----------
CREATE TABLE IF NOT EXISTS evaluation_cases (
    id                     BIGSERIAL   PRIMARY KEY,
    eval_id                TEXT        NOT NULL UNIQUE,
    query                  TEXT        NOT NULL,
    category               TEXT,       -- testcase_search / defect_search / doc_qa / cross_version / multi_condition / unanswerable
    expected_intent        TEXT,
    expected_filters       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    gold_documents         TEXT[]      NOT NULL DEFAULT '{}',
    gold_chunks            TEXT[]      NOT NULL DEFAULT '{}',
    expected_answer_points JSONB       NOT NULL DEFAULT '[]'::jsonb,
    is_answerable          BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at             TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_cases_category
    ON evaluation_cases (category);

-- ---------- 单次评测运行的汇总结果 ----------
CREATE TABLE IF NOT EXISTS evaluation_results (
    id             BIGSERIAL   PRIMARY KEY,
    run_id         TEXT        NOT NULL,                   -- 同一次实验的批次标识
    eval_id        TEXT        NOT NULL REFERENCES evaluation_cases (eval_id) ON DELETE CASCADE,
    config         JSONB       NOT NULL DEFAULT '{}'::jsonb, -- chunk_size / embedding / topk / rerank ...
    recall_at_5    DOUBLE PRECISION,
    recall_at_10   DOUBLE PRECISION,
    precision_at_k DOUBLE PRECISION,
    mrr            DOUBLE PRECISION,
    ndcg_at_k      DOUBLE PRECISION,
    faithfulness   DOUBLE PRECISION,
    answer_relevancy DOUBLE PRECISION,
    citation_correctness DOUBLE PRECISION,
    latency_ms     INTEGER,
    token_usage    INTEGER,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_eval_results_run
    ON evaluation_results (run_id);
CREATE INDEX IF NOT EXISTS idx_eval_results_eval
    ON evaluation_results (eval_id);

-- ---------- Badcase 闭环 ----------
CREATE TABLE IF NOT EXISTS badcases (
    id             BIGSERIAL   PRIMARY KEY,
    run_id         TEXT,
    eval_id        TEXT,
    query          TEXT        NOT NULL,
    failure_stage  TEXT,       -- parsing / chunking / embedding / query_understanding / retrieval / rerank / context / generation / citation
    root_cause     TEXT,
    hypothesis     TEXT,
    fix            TEXT,
    status         TEXT        NOT NULL DEFAULT 'open',   -- open / fixed / verified / wontfix
    detail         JSONB       NOT NULL DEFAULT '{}'::jsonb, -- 各阶段召回明细快照
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_badcases_stage
    ON badcases (failure_stage, status);
