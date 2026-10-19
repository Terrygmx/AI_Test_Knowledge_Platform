#!/usr/bin/env python3
"""真实 Embedding 相似度探针（Phase 2.1）。

默认调用本机 OMLX，无需真实 Key；远端兼容服务才需要配置 EMBEDDING_API_KEY。
输出成对 cosine similarity，供人工判断语义模型是否工作。
不要把输出当作 Recall / MRR，真正的检索质量需要人工 Gold Set。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.exceptions import DomainError
from app.retrieval.embedding import get_embedding_client

PAIRS = [
    ("支付失败", "支付错误"),
    ("支付失败", "登录成功"),
    ("登录失败", "无法登录"),
    ("P0 支付缺陷", "支付问题"),
]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right, strict=True))
    left_norm = sum(a * a for a in left) ** 0.5
    right_norm = sum(b * b for b in right) ** 0.5
    if left_norm == 0 or right_norm == 0:
        return 0.0
    return dot / (left_norm * right_norm)


def main() -> None:
    parser = argparse.ArgumentParser(description="Embedding 相似度探针")
    args, unknown = parser.parse_known_args()
    if unknown:
        print(f"忽略无法识别的参数：{' '.join(unknown)}（内置测试对是默认且唯一的模式）")
    del args

    try:
        client = get_embedding_client()
    except DomainError as exc:
        raise SystemExit(f"Embedding 不可用：{exc.message}") from exc
    print(f"model_dimension={client.dimension}")
    texts = sorted({text for pair in PAIRS for text in pair})
    vectors = dict(zip(texts, client.embed_texts(texts), strict=True))
    for left, right in PAIRS:
        score = cosine_similarity(vectors[left], vectors[right])
        print(f"{left} <-> {right}: {score:.4f}")


if __name__ == "__main__":
    main()
