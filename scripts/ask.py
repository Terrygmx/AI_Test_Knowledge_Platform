#!/usr/bin/env python3
"""Phase 3 最小 RAG 命令行入口。

用法：
    uv run python scripts/ask.py "登录失败怎么测试？"
    uv run python scripts/ask.py --top-k 3 --source-type defect "支付失败有哪些根因？"

需要 PostgreSQL + pgvector、EMBEDDING_API_KEY 与可用的 LLM（默认本地 Ollama）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import SessionLocal
from app.generation.llm import get_llm_client
from app.generation.rag import RAGService
from app.retrieval.embedding import get_embedding_client


def main() -> None:
    parser = argparse.ArgumentParser(description="基于测试知识库回答问题")
    parser.add_argument("question", help="自然语言问题")
    parser.add_argument("--top-k", type=int, default=5, help="证据片段数，1-20")
    parser.add_argument("--max-distance", type=float, default=None, help="cosine distance 上限")
    parser.add_argument(
        "--source-type",
        action="append",
        choices=["testcase", "defect", "requirement", "document"],
        help="限定来源，可重复传入",
    )
    args = parser.parse_args()
    if not 1 <= args.top_k <= 20:
        parser.error("--top-k 必须在 1 到 20 之间")

    with SessionLocal() as session:
        result = RAGService.answer(
            session,
            question=args.question,
            embedding_client=get_embedding_client(),
            llm_factory=get_llm_client,
            top_k=args.top_k,
            source_types=args.source_type,
            max_distance=args.max_distance,
        )

    print(f"问题：{result.question}")
    print(f"回答：{result.answer}")
    print(f"引用合法：{result.citation_valid}")
    if result.evidence_sufficient is not None:
        print(f"证据充分：{result.evidence_sufficient}")
    if result.citations:
        print("引用：")
        for citation in result.citations:
            print(
                f"- {citation.source_type}:{citation.source_id}"
                f"#chunk-{citation.chunk_index}"
            )


if __name__ == "__main__":
    main()
