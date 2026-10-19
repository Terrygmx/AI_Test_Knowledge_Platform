"""Phase 7 Embedding 对比实验的纯逻辑。

实验固定 Phase 6 的 ``entity+heading`` 切分基线，只替换 Embedding 模型。
不同模型可能返回不同维度，而 pgvector 的列维度在建表时固定；因此每个模型
使用独立的实验表，避免截断或补零改变向量空间。
"""

from __future__ import annotations

import math
import re
import statistics
import time
from collections.abc import Sequence
from dataclasses import dataclass

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    select,
)
from sqlalchemy.engine import Engine

from app.evaluation.dataset import ChunkingDataset
from app.evaluation.metrics import RetrievalEvaluation, percentile
from app.models.types import POSTGRES
from app.retrieval.embedding import EmbeddingClient
from app.schemas.knowledge_chunk import KnowledgeChunkCreate


@dataclass(frozen=True)
class EmbeddingConfig:
    """一个待比较的模型；``name`` 是报告中的稳定标识。"""

    name: str
    model: str
    dimension: int


@dataclass(frozen=True)
class RetrievedChunk:
    """评测指标只需要来源与正文，不依赖实验表的 ORM 身份。"""

    source_type: str
    source_id: str
    content: str


@dataclass(frozen=True)
class EmbeddingResult:
    name: str
    model: str
    dimension: int
    table_name: str
    chunk_count: int
    warmup_seconds: float
    content_embedding_seconds: float
    write_seconds: float
    index_seconds: float
    query_embedding_p50_seconds: float
    query_embedding_p95_seconds: float
    search_p50_seconds: float
    search_p95_seconds: float
    query_total_p50_seconds: float
    query_total_p95_seconds: float
    metrics: dict[str, float | int]


def build_engine(database_url: str, *, allow_sqlite: bool = False) -> Engine:
    """实验库名必须包含 embedding，避免误写开发库。"""
    if database_url.startswith("sqlite"):
        if not allow_sqlite:
            raise ValueError("默认拒绝写入 SQLite；离线演练需显式 allow_sqlite=True")
        return create_engine(database_url)

    database_name = database_url.rsplit("/", 1)[-1].split("?", 1)[0]
    if "embedding" not in database_name:
        raise ValueError(f"实验数据库名必须包含 embedding：{database_name}")
    return create_engine(database_url, pool_pre_ping=True)


def parse_model_specs(value: str) -> list[EmbeddingConfig]:
    """解析 ``model:dimension,...``，名称与模型名相同。"""
    configs: list[EmbeddingConfig] = []
    names: set[str] = set()
    for raw in value.split(","):
        item = raw.strip()
        if not item:
            continue
        model, separator, dimension_text = item.rpartition(":")
        if not separator or not model.strip():
            raise ValueError(f"模型配置格式应为 model:dimension：{item}")
        try:
            dimension = int(dimension_text)
        except ValueError as exc:
            raise ValueError(f"模型维度必须是整数：{item}") from exc
        if dimension <= 0:
            raise ValueError(f"模型维度必须大于 0：{item}")
        if model in names:
            raise ValueError(f"模型重复：{model}")
        names.add(model)
        configs.append(EmbeddingConfig(name=model, model=model, dimension=dimension))
    if not configs:
        raise ValueError("至少需要一个模型配置")
    return configs


def _safe_table_name(config: EmbeddingConfig) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", config.name.lower()).strip("_")
    return f"phase7_embedding_{slug}_{config.dimension}"


def _experiment_table(config: EmbeddingConfig) -> Table:
    metadata = MetaData()
    return Table(
        _safe_table_name(config),
        metadata,
        Column("id", Integer, primary_key=True, autoincrement=True),
        Column("source_type", String(32), nullable=False),
        Column("source_id", String(64), nullable=False),
        Column("chunk_index", Integer, nullable=False),
        Column("content", Text, nullable=False),
        Column("embedding", Vector(config.dimension), nullable=False),
    )


def _validate_vectors(
    vectors: Sequence[Sequence[float]],
    *,
    expected_count: int,
    dimension: int,
) -> list[list[float]]:
    if len(vectors) != expected_count:
        raise ValueError(
            f"Embedding 数量不一致：expected={expected_count}, actual={len(vectors)}"
        )
    normalized: list[list[float]] = []
    for index, vector in enumerate(vectors):
        if len(vector) != dimension:
            raise ValueError(
                f"Embedding 维度不一致：index={index}, "
                f"expected={dimension}, actual={len(vector)}"
            )
        values = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in values):
            raise ValueError(f"Embedding 包含非有限值：index={index}")
        normalized.append(values)
    return normalized


def evaluate_embedding(
    engine: Engine,
    dataset: ChunkingDataset,
    chunks: Sequence[KnowledgeChunkCreate],
    config: EmbeddingConfig,
    embedding: EmbeddingClient,
    *,
    top_k: int = 10,
) -> EmbeddingResult:
    """在同一 Chunk 集合上重建模型专属向量表并计算检索指标。"""
    if engine.dialect.name != POSTGRES:
        raise ValueError("真实 Embedding 实验只支持 PostgreSQL + pgvector")
    if embedding.dimension != config.dimension:
        raise ValueError(
            f"模型维度不一致：{config.name} 配置 {config.dimension}，"
            f"客户端返回 {embedding.dimension}"
        )

    warmup_started = time.perf_counter()
    embedding.embed_query("embedding experiment warmup")
    warmup_seconds = time.perf_counter() - warmup_started

    table = _experiment_table(config)
    table.drop(engine, checkfirst=True)
    table.create(engine)

    embedding_started = time.perf_counter()
    vectors = _validate_vectors(
        embedding.embed_texts([chunk.content for chunk in chunks]),
        expected_count=len(chunks),
        dimension=config.dimension,
    )
    content_embedding_seconds = time.perf_counter() - embedding_started

    write_started = time.perf_counter()
    with engine.begin() as connection:
        connection.execute(
            table.insert(),
            [
                {
                    "source_type": chunk.source_type,
                    "source_id": chunk.source_id,
                    "chunk_index": chunk.chunk_index,
                    "content": chunk.content,
                    "embedding": vector,
                }
                for chunk, vector in zip(chunks, vectors, strict=True)
            ],
        )
    write_seconds = time.perf_counter() - write_started

    evaluator = RetrievalEvaluation(k_values=(5, 10))
    query_embedding_latencies: list[float] = []
    search_latencies: list[float] = []
    total_latencies: list[float] = []
    with engine.connect() as connection:
        for query in dataset.queries:
            query_started = time.perf_counter()
            query_vector = embedding.embed_query(query.query)
            embedded_at = time.perf_counter()
            query_embedding_latencies.append(embedded_at - query_started)

            distance = table.c.embedding.cosine_distance(query_vector).label("distance")
            rows = connection.execute(
                select(
                    table.c.source_type,
                    table.c.source_id,
                    table.c.content,
                    distance,
                )
                .order_by(distance)
                .limit(top_k)
            ).all()
            searched_at = time.perf_counter()
            search_latencies.append(searched_at - embedded_at)
            total_latencies.append(searched_at - query_started)

            evaluator.add(
                query,
                [
                    RetrievedChunk(
                        source_type=row.source_type,
                        source_id=row.source_id,
                        content=row.content,
                    )
                    for row in rows
                ],
                distances=[float(row.distance) for row in rows],
            )

    return EmbeddingResult(
        name=config.name,
        model=config.model,
        dimension=config.dimension,
        table_name=table.name,
        chunk_count=len(chunks),
        warmup_seconds=warmup_seconds,
        content_embedding_seconds=content_embedding_seconds,
        write_seconds=write_seconds,
        index_seconds=content_embedding_seconds + write_seconds,
        query_embedding_p50_seconds=percentile(query_embedding_latencies, 0.5),
        query_embedding_p95_seconds=percentile(query_embedding_latencies, 0.95),
        search_p50_seconds=percentile(search_latencies, 0.5),
        search_p95_seconds=percentile(search_latencies, 0.95),
        query_total_p50_seconds=percentile(total_latencies, 0.5),
        query_total_p95_seconds=percentile(total_latencies, 0.95),
        metrics=evaluator.summary(),
    )


def chunk_length_summary(chunks: Sequence[KnowledgeChunkCreate]) -> dict[str, float | int]:
    lengths = [len(chunk.content) for chunk in chunks]
    return {
        "chunk_count": len(chunks),
        "average_chars": statistics.mean(lengths) if lengths else 0.0,
        "median_chars": statistics.median(lengths) if lengths else 0.0,
        "max_chars": max(lengths, default=0),
    }


def run_embedding_experiment(
    engine: Engine,
    dataset: ChunkingDataset,
    chunks: Sequence[KnowledgeChunkCreate],
    configs: Sequence[EmbeddingConfig],
    clients: Sequence[EmbeddingClient],
    *,
    top_k: int = 10,
) -> list[EmbeddingResult]:
    """串行评测多个模型；固定同一 Chunk 集合与 Query 集。"""
    if len(configs) != len(clients):
        raise ValueError("configs 与 clients 数量必须一致")
    return [
        evaluate_embedding(
            engine,
            dataset,
            chunks,
            config,
            client,
            top_k=top_k,
        )
        for config, client in zip(configs, clients, strict=True)
    ]


__all__ = [
    "EmbeddingConfig",
    "EmbeddingResult",
    "RetrievedChunk",
    "build_engine",
    "chunk_length_summary",
    "evaluate_embedding",
    "parse_model_specs",
    "run_embedding_experiment",
]
