"""Phase 6 文档切分策略的离线测试。"""

from __future__ import annotations

import pytest

from app.retrieval.chunking import FixedChunker, SemanticChunker
from app.retrieval.embedding import FakeEmbeddingClient


class KeywordEmbedding:
    """离线语义测试替身：让相同主题的句子向量更接近。"""

    dimension = 4

    def embed_texts(self, texts):
        return [self.embed_query(text) for text in texts]

    def embed_query(self, text):
        groups = [
            ("支付", "扣款", "退款"),
            ("登录", "密码", "账户"),
            ("配送", "运费", "购物车"),
        ]
        vector = [0.0, 0.0, 0.0, 0.01]
        for index, words in enumerate(groups):
            if any(word in text for word in words):
                vector[index] = 1.0
        return vector


class TestFixedChunker:
    def test_never_exceeds_character_limit(self) -> None:
        text = "\n\n".join(f"第 {index} 段包含一些测试说明。" for index in range(30))

        chunks = FixedChunker(max_chars=80, overlap_chars=16).split(text)

        assert chunks
        assert all(len(chunk.content) <= 80 for chunk in chunks)
        assert len(chunks) > 1

    def test_offsets_point_back_to_original_text(self) -> None:
        text = "# 标题\n\n第一段正文。\n\n第二段正文。\n"

        chunks = FixedChunker(max_chars=20, overlap_chars=0).split(text)

        for chunk in chunks:
            assert text[chunk.start : chunk.end]
            if chunk.content in text:
                assert text[chunk.start : chunk.end] == chunk.content

    def test_overlap_does_not_chain_accumulate(self) -> None:
        text = "\n\n".join(["A" * 40, "B" * 40, "C" * 40])

        chunks = FixedChunker(max_chars=50, overlap_chars=10).split(text)

        assert all(len(chunk.content) <= 50 for chunk in chunks)
        assert "A" * 10 not in chunks[-1].content

    def test_blank_text_has_no_chunks(self) -> None:
        assert FixedChunker().split(" \n\n ") == []

    @pytest.mark.parametrize(
        ("max_chars", "overlap_chars"),
        [(0, 0), (10, -1), (10, 10), (10, 11)],
    )
    def test_rejects_invalid_configuration(self, max_chars: int, overlap_chars: int) -> None:
        with pytest.raises(ValueError):
            FixedChunker(max_chars=max_chars, overlap_chars=overlap_chars)

    def test_long_sentence_is_hard_split(self) -> None:
        chunks = FixedChunker(max_chars=10, overlap_chars=0).split("无标点长句" * 10)

        assert len(chunks) == 5
        assert all(len(chunk.content) == 10 for chunk in chunks)


class TestSemanticChunker:
    def test_splits_at_low_similarity_boundary(self) -> None:
        text = (
            "支付失败后先查询网关。扣款成功时补写支付记录。"
            "登录账户需要校验密码。账户锁定后联系客服。"
        )

        chunks = SemanticChunker(
            embedding_client=KeywordEmbedding(),
            max_chars=200,
            threshold=0.5,
            window=1,
        ).split(text)

        assert len(chunks) == 2
        assert "支付" in chunks[0].content
        assert "账户" in chunks[1].content

    def test_keeps_groups_under_character_limit(self) -> None:
        text = "支付扣款退款说明。" * 20

        chunks = SemanticChunker(
            embedding_client=KeywordEmbedding(),
            max_chars=40,
            threshold=0.1,
            window=1,
        ).split(text)

        assert chunks
        assert all(len(chunk.content) <= 40 for chunk in chunks)

    def test_single_sentence_falls_back_to_fixed_budget(self) -> None:
        text = "只有一句话。"

        chunks = SemanticChunker(
            embedding_client=KeywordEmbedding(), max_chars=200
        ).split(text)

        assert [chunk.content for chunk in chunks] == [text]

    def test_markdown_heading_stays_with_section_body(self) -> None:
        text = (
            "# 文档\n\n"
            "## 支付\n\n"
            "支付失败后查询网关。扣款成功时补写支付记录。\n\n"
            "## 登录\n\n"
            "登录账户需要校验密码。账户锁定后联系客服。"
        )

        chunks = SemanticChunker(
            embedding_client=KeywordEmbedding(),
            max_chars=200,
            threshold=0.5,
            window=1,
        ).split(text)

        assert len(chunks) >= 2
        assert all(chunk.content.strip() != "# 文档" for chunk in chunks)
        assert any(
            "## 支付" in chunk.content and "查询网关" in chunk.content
            for chunk in chunks
        )
        assert any(
            "## 登录" in chunk.content and "校验密码" in chunk.content
            for chunk in chunks
        )

    def test_blank_text_has_no_chunks(self) -> None:
        chunks = SemanticChunker(embedding_client=FakeEmbeddingClient()).split("")

        assert chunks == []
