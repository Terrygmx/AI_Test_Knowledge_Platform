"""KnowledgeChunk 服务：创建检索单元、生成 Embedding、查询向量。

关键边界：

- 入参不接收向量，Embedding 由服务层生成，避免调用方塞入不可追溯的向量；
- 内容哈希未传时由服务层计算；
- 向量维度必须与配置一致，否则在写入前拒绝；
- 向量距离排序只在 PostgreSQL 执行，SQLite 返回明确错误而不是伪装成向量检索。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.exceptions import DomainError, DuplicateError, NotFoundError
from app.core.logging import get_logger
from app.models import KnowledgeChunk
from app.models.types import POSTGRES
from app.retrieval.embedding import EmbeddingClient, content_sha256
from app.retrieval.filters import apply_metadata_filters
from app.retrieval.hybrid import ChannelHit, FusedHit, fuse
from app.retrieval.lexical import BM25Index, LexicalDocument
from app.schemas.knowledge_chunk import KnowledgeChunkCreate

logger = get_logger(__name__)


class KnowledgeChunkService:
    """检索单元的写入与向量读取。"""

    #: 与 ``KnowledgeChunk.embedding`` 的 ``Vector(1024)`` 保持一致
    embedding_dimension = 1024

    @classmethod
    def create(
        cls,
        session: Session,
        *,
        data: KnowledgeChunkCreate,
        embedding_client: EmbeddingClient | None = None,
    ) -> KnowledgeChunk:
        """创建 Chunk。

        ``embedding_client`` 为 None 时只保存正文与 Metadata，embedding 留空；
        显式传入客户端时立即生成向量。这样 Phase 2 可以在没有 Key 的环境中保存数据，
        也便于测试纯 CRUD 路径。
        """
        values = data.to_orm_dict()
        values["content_hash"] = values.get("content_hash") or content_sha256(data.content)
        values["meta"] = values.pop("metadata", {})

        if embedding_client is not None:
            vector = embedding_client.embed_query(data.content)
            cls._validate_dimension(vector, cls.embedding_dimension)
            values["embedding"] = vector

        chunk = KnowledgeChunk(**values)
        session.add(chunk)
        cls._commit(
            session,
            source_type=data.source_type,
            source_id=data.source_id,
            chunk_index=data.chunk_index,
        )
        logger.info(
            "创建知识片段 source=%s:%s chunk=%s",
            chunk.source_type,
            chunk.source_id,
            chunk.chunk_index,
        )
        return chunk

    @classmethod
    def create_many(
        cls,
        session: Session,
        *,
        items: Sequence[KnowledgeChunkCreate],
        embedding_client: EmbeddingClient,
        replace_source_types: Sequence[str] | None = None,
    ) -> list[KnowledgeChunk]:
        """批量创建，并为所有 Chunk 生成 Embedding。

        ``replace_source_types`` 用于索引重建：先完成 Embedding，再在同一事务中
        删除旧 Chunk。上游失败时不会提前提交删除。
        """
        if not items:
            return []
        vectors = embedding_client.embed_texts([item.content for item in items])
        for vector in vectors:
            cls._validate_dimension(vector, cls.embedding_dimension)

        if replace_source_types:
            session.execute(
                delete(KnowledgeChunk).where(
                    KnowledgeChunk.source_type.in_(list(replace_source_types))
                )
            )

        chunks: list[KnowledgeChunk] = []
        for item, vector in zip(items, vectors, strict=True):
            values = item.to_orm_dict()
            values["content_hash"] = values.get("content_hash") or content_sha256(item.content)
            values["meta"] = values.pop("metadata", {})
            values["embedding"] = vector
            chunks.append(KnowledgeChunk(**values))

        session.add_all(chunks)
        cls._commit(session)
        return chunks

    @classmethod
    def list(
        cls,
        session: Session,
        *,
        offset: int = 0,
        limit: int = 20,
        source_type: str | None = None,
        source_id: str | None = None,
        has_embedding: bool | None = None,
    ) -> tuple[list[KnowledgeChunk], int]:
        stmt = select(KnowledgeChunk)
        if source_type:
            stmt = stmt.where(KnowledgeChunk.source_type == source_type)
        if source_id:
            stmt = stmt.where(KnowledgeChunk.source_id == source_id)
        if has_embedding is True:
            stmt = stmt.where(KnowledgeChunk.embedding.is_not(None))
        elif has_embedding is False:
            stmt = stmt.where(KnowledgeChunk.embedding.is_(None))
        total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
        rows = session.scalars(
            stmt.order_by(KnowledgeChunk.id).limit(limit).offset(offset)
        ).all()
        return list(rows), total

    @classmethod
    def get_or_404(cls, session: Session, chunk_id: int) -> KnowledgeChunk:
        chunk = session.get(KnowledgeChunk, chunk_id)
        if chunk is None:
            raise NotFoundError(f"知识片段不存在：id={chunk_id}")
        return chunk

    @classmethod
    def delete(cls, session: Session, chunk: KnowledgeChunk) -> None:
        session.delete(chunk)
        cls._commit(session)

    @classmethod
    def search(
        cls,
        session: Session,
        *,
        query_embedding: Sequence[float],
        limit: int = 10,
        source_types: Sequence[str] | None = None,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[tuple[KnowledgeChunk, float]]:
        """按 cosine distance 返回 ``(chunk, distance)``。

        距离越小越相似。只有 PostgreSQL + pgvector 支持 ``<=>``；SQLite
        下明确报错，避免测试环境返回假排序。
        """
        if session.bind is None or session.bind.dialect.name != POSTGRES:
            raise DomainError(
                "向量检索仅支持 PostgreSQL + pgvector",
                detail={"dialect": session.bind.dialect.name if session.bind else None},
            )
        if limit < 1:
            return []

        vector = list(query_embedding)
        distance = KnowledgeChunk.embedding.cosine_distance(vector)
        stmt = select(KnowledgeChunk, distance.label("distance"))
        stmt = stmt.where(KnowledgeChunk.embedding.is_not(None))
        if source_types:
            stmt = stmt.where(KnowledgeChunk.source_type.in_(list(source_types)))
        stmt = apply_metadata_filters(
            stmt,
            metadata_filters,
            dialect_name=session.bind.dialect.name,
        )
        stmt = stmt.order_by(
            distance,
            KnowledgeChunk.source_type,
            KnowledgeChunk.source_id,
            KnowledgeChunk.chunk_index,
            KnowledgeChunk.id,
        ).limit(limit)
        results = [(chunk, float(value)) for chunk, value in session.execute(stmt).all()]
        logger.info(
            "向量检索完成 hits=%s distances=%s",
            len(results),
            [round(distance, 4) for _, distance in results],
        )
        return results

    @classmethod
    def lexical_search(
        cls,
        session: Session,
        *,
        query: str,
        limit: int = 10,
        source_types: Sequence[str] | None = None,
        metadata_filters: dict[str, object] | None = None,
    ) -> list[tuple[KnowledgeChunk, float, list[str]]]:
        """按 BM25 返回 ``(chunk, score, matched_terms)``。

        Phase 8 的应用层实现不依赖 PostgreSQL 扩展，SQLite 与 PostgreSQL
        使用同一套分词和排序。每次查询从数据库读取候选 Chunk 并建内存倒排索引，
        适用于当前小规模知识库；数据量增大后应改为持久化倒排表 / 搜索服务，
        不能把这个实现当作大库的性能方案。
        """
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        stmt = select(KnowledgeChunk)
        if source_types:
            stmt = stmt.where(KnowledgeChunk.source_type.in_(list(source_types)))
        # 过滤必须在读取候选前完成。先取 limit 再过滤会发生候选饥饿：
        # 目标 Chunk 可能排在第 N 位，而前 N-1 位全被过滤掉。
        stmt = apply_metadata_filters(
            stmt,
            metadata_filters,
            dialect_name=session.bind.dialect.name,
        )
        chunks = list(session.scalars(stmt.order_by(KnowledgeChunk.id)).all())
        if not chunks:
            return []

        index = BM25Index(
            [
                LexicalDocument(
                    source_type=chunk.source_type,
                    source_id=chunk.source_id,
                    content=chunk.content,
                )
                for chunk in chunks
            ]
        )
        hits = index.search(query, limit=limit)
        results: list[tuple[KnowledgeChunk, float, list[str]]] = []
        for hit in hits:
            results.append((chunks[hit.document_index], hit.score, hit.matched_terms))
        logger.info(
            "BM25 检索完成 hits=%s terms=%s",
            len(results),
            [terms for _, _, terms in results],
        )
        return results

    @classmethod
    def hybrid_search(
        cls,
        session: Session,
        *,
        query: str,
        query_embedding: Sequence[float],
        candidate_k: int | None = None,
        vector_candidate_k: int | None = None,
        keyword_candidate_k: int | None = None,
        limit: int | None = None,
        source_types: Sequence[str] | None = None,
        metadata_filters: dict[str, object] | None = None,
        strategy: str | None = None,
        vector_weight: float = 1.0,
        keyword_weight: float = 1.0,
        rrf_k: int | None = None,
    ) -> list[FusedHit]:
        """融合 Vector 与 BM25 候选，返回保留 Chunk 身份的可追溯结果。

        这是 Phase 9 的 Chunk 检索接缝，不改变统一 ``/search`` 的 HTTP 契约。
        Vector 候选由 pgvector 生成，Keyword 候选复用 Phase 8 的 BM25 索引。
        两者都按 ``(source_type, source_id, chunk_index)`` 去重，避免同一来源的
        不同证据片段被合并。

        两个通道的默认候选深度分别来自 ``retrieval_vector_top_k`` 与
        ``retrieval_keyword_top_k``。``candidate_k`` 是兼容旧调用的共同覆盖值，
        显式通道值优先于它。当前实现仍会在每次 BM25 查询时读取并建立内存索引，
        适用于小规模知识库；接入更大数据量前必须先解决候选预过滤与倒排表持久化。
        """
        if not query.strip():
            raise ValueError("query 不能为空")
        if not math.isfinite(vector_weight) or not math.isfinite(keyword_weight):
            raise ValueError("通道权重必须是有限数")

        # ``or`` 会把显式 0 变成默认值；先从调用方校验非法值，再应用默认配置。
        if candidate_k is not None and candidate_k < 1:
            raise ValueError("candidate_k 必须大于 0")
        if vector_candidate_k is not None and vector_candidate_k < 1:
            raise ValueError("vector_candidate_k 必须大于 0")
        if keyword_candidate_k is not None and keyword_candidate_k < 1:
            raise ValueError("keyword_candidate_k 必须大于 0")
        if limit is not None and limit < 1:
            raise ValueError("limit 必须大于 0")
        if rrf_k is not None and rrf_k < 1:
            raise ValueError("rrf_k 必须大于 0")
        shared_candidate_k = candidate_k
        vector_depth = (
            vector_candidate_k
            if vector_candidate_k is not None
            else shared_candidate_k
            if shared_candidate_k is not None
            else settings.retrieval_vector_top_k
        )
        keyword_depth = (
            keyword_candidate_k
            if keyword_candidate_k is not None
            else shared_candidate_k
            if shared_candidate_k is not None
            else settings.retrieval_keyword_top_k
        )
        final_limit = limit if limit is not None else settings.retrieval_final_top_k
        selected_strategy = strategy or settings.retrieval_fusion
        selected_rrf_k = rrf_k if rrf_k is not None else settings.retrieval_rrf_k
        if min(vector_depth, keyword_depth) < final_limit:
            raise ValueError("通道候选深度不能小于 limit")
        if session.bind is None or session.bind.dialect.name != POSTGRES:
            raise DomainError(
                "混合检索仅支持 PostgreSQL + pgvector",
                detail={"dialect": session.bind.dialect.name if session.bind else None},
            )

        vector_results = cls.search(
            session,
            query_embedding=query_embedding,
            limit=vector_depth,
            source_types=source_types,
            metadata_filters=metadata_filters,
        )
        keyword_results = cls.lexical_search(
            session,
            query=query,
            limit=keyword_depth,
            source_types=source_types,
            metadata_filters=metadata_filters,
        )
        vector_hits = [
            ChannelHit(
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                chunk_index=chunk.chunk_index,
                score=distance,
                content=chunk.content,
                higher_is_better=False,
            )
            for chunk, distance in vector_results
        ]
        keyword_hits = [
            ChannelHit(
                source_type=chunk.source_type,
                source_id=chunk.source_id,
                chunk_index=chunk.chunk_index,
                score=score,
                content=chunk.content,
                higher_is_better=True,
                matched_terms=tuple(matched_terms),
            )
            for chunk, score, matched_terms in keyword_results
        ]
        return fuse(
            vector_hits,
            keyword_hits,
            strategy=selected_strategy,
            vector_weight=vector_weight,
            keyword_weight=keyword_weight,
            rrf_k=selected_rrf_k,
            limit=final_limit,
        )

    @classmethod
    def backfill_embeddings(
        cls,
        session: Session,
        *,
        embedding_client: EmbeddingClient,
        source_types: Sequence[str] | None = None,
        batch_size: int | None = None,
    ) -> int:
        """给缺向量的 Chunk 补齐 Embedding（原地更新，不重建正文）。

        Phase 5 的文档导入刻意不调用 Embedding（避免大批量导入与模型服务耦合），
        因此 document Chunk 入库时向量为空。这里是补救入口：不重新切分正文，
        只补向量，否则这部分知识永远进不了向量检索。

        按 ``batch_size``（默认 ``EMBEDDING_BATCH_SIZE``）分批提交：上游在第 N 批
        失败时，已成功的批次仍然保留，不必重跑全部。异常照常抛出，
        调用方据此知道「补到一半」，返回值是实际写入的条数。
        """
        return cls._embed_in_batches(
            session=session,
            embedding_client=embedding_client,
            batch_size=batch_size,
            action="补齐向量",
            detail={"source_types": list(source_types or [])},
            load_batch=lambda after_id, size: cls._load_missing_embedding_batch(
                session,
                after_id=after_id,
                size=size,
                source_types=source_types,
            ),
        )

    @classmethod
    def reembed_chunks(
        cls,
        session: Session,
        *,
        embedding_client: EmbeddingClient,
        source_types: Sequence[str],
        batch_size: int | None = None,
    ) -> int:
        """强制重算指定来源的全部向量（含已有向量的 Chunk）。

        切分策略或 Embedding 模型变更后，``backfill_embeddings`` 只补空缺，
        旧向量不会被刷新；这个入口用于显式重建（Phase 6 Chunking 实验需要）。
        """
        return cls._embed_in_batches(
            session=session,
            embedding_client=embedding_client,
            batch_size=batch_size,
            action="重算向量",
            detail={"source_types": list(source_types)},
            load_batch=lambda after_id, size: cls._load_source_batch(
                session,
                after_id=after_id,
                size=size,
                source_types=source_types,
            ),
        )

    @classmethod
    def _embed_in_batches(
        cls,
        session: Session,
        *,
        embedding_client: EmbeddingClient,
        batch_size: int | None,
        action: str,
        detail: dict[str, object],
        load_batch: Callable[[int, int], Sequence[KnowledgeChunk]],
    ) -> int:
        """分批生成向量并逐批提交。

        逐批提交是刻意的：一次性 commit 时上游在第 N 批失败会让前面已算出的
        向量一起回滚，大批量补向量就变成「要么全成功要么全无」。
        ``load_batch`` 同时负责分页，避免一次性把全部 Chunk 读进内存。
        """
        size = max(batch_size or settings.embedding_batch_size, 1)
        written = 0
        after_id = 0
        while True:
            chunks = list(load_batch(after_id, size))
            if not chunks:
                break
            after_id = chunks[-1].id

            # 空白正文无法生成向量（上游会拒绝），跳过而不是让整批失败
            batch = [chunk for chunk in chunks if chunk.content.strip()]
            if not batch:
                continue

            vectors = embedding_client.embed_texts([chunk.content for chunk in batch])
            try:
                for chunk, vector in zip(batch, vectors, strict=True):
                    cls._validate_dimension(vector, cls.embedding_dimension)
                    chunk.embedding = vector
                cls._commit(session)
            except Exception:
                # 已提交的批次保留；当前失败批次的脏对象不能泄漏到调用方的后续事务
                session.rollback()
                raise

            written += len(batch)
            logger.info("%s进度 written=%s detail=%s", action, written, detail)
        return written

    @classmethod
    def _load_missing_embedding_batch(
        cls,
        session: Session,
        *,
        after_id: int,
        size: int,
        source_types: Sequence[str] | None,
    ) -> Sequence[KnowledgeChunk]:
        """按主键游标读取一批缺向量 Chunk，避免 OFFSET 随进度变慢。"""
        stmt = (
            select(KnowledgeChunk)
            .where(KnowledgeChunk.id > after_id, KnowledgeChunk.embedding.is_(None))
            .order_by(KnowledgeChunk.id)
            .limit(size)
        )
        if source_types:
            stmt = stmt.where(KnowledgeChunk.source_type.in_(list(source_types)))
        return session.scalars(stmt).all()

    @classmethod
    def _load_source_batch(
        cls,
        session: Session,
        *,
        after_id: int,
        size: int,
        source_types: Sequence[str],
    ) -> Sequence[KnowledgeChunk]:
        """按主键游标读取一批指定来源的 Chunk，用于强制重算。"""
        stmt = (
            select(KnowledgeChunk)
            .where(
                KnowledgeChunk.id > after_id,
                KnowledgeChunk.source_type.in_(list(source_types)),
            )
            .order_by(KnowledgeChunk.id)
            .limit(size)
        )
        return session.scalars(stmt).all()

    @classmethod
    def count_without_embedding(cls, session: Session, source_type: str | None = None) -> int:
        stmt = select(func.count()).where(KnowledgeChunk.embedding.is_(None))
        if source_type:
            stmt = stmt.where(KnowledgeChunk.source_type == source_type)
        return session.scalar(stmt) or 0

    @classmethod
    def count_with_embedding(
        cls, session: Session, *, source_id: str, source_type: str = "document"
    ) -> int:
        """已生成向量的 Chunk 数，用于导入前判断「替换会清掉已有索引」。"""
        return (
            session.scalar(
                select(func.count()).where(
                    KnowledgeChunk.source_type == source_type,
                    KnowledgeChunk.source_id == source_id,
                    KnowledgeChunk.embedding.is_not(None),
                )
            )
            or 0
        )

    @classmethod
    def _validate_dimension(cls, vector: Sequence[float], expected: int) -> None:
        if len(vector) != expected:
            raise DomainError(
                "Embedding 维度与配置不一致",
                detail={"expected": expected, "actual": len(vector)},
            )

    @classmethod
    def _commit(
        cls,
        session: Session,
        *,
        source_type: str | None = None,
        source_id: str | None = None,
        chunk_index: int | None = None,
    ) -> None:
        """把唯一约束冲突转换成统一错误信封（409 duplicate）。"""
        try:
            session.flush()
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            detail = {
                "source_type": source_type,
                "source_id": source_id,
                "chunk_index": chunk_index,
            }
            logger.warning("知识片段写入冲突：%s", detail)
            raise DuplicateError("知识片段已存在", detail=detail) from exc


__all__ = ["KnowledgeChunkService"]
