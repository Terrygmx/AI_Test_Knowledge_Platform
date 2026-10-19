"""应用层词法检索：可解释的分词、倒排索引与 BM25。

本模块不依赖 PostgreSQL 扩展，SQLite 测试与 PostgreSQL 服务都走同一套逻辑。
分词策略针对测试知识中的混合文本：

- ASCII 标识符、版本号、优先级保留完整 token，例如 ``BUG-1234``、``v1.3``、``P0``；
- 连续中文生成 unigram + bigram，既保留单字召回，又能表达常见双字词；
- 不做停用词删除，避免把 ``不``、``无`` 等否定词从证据中抹掉。

BM25 分数只在词法检索内部用于排序，不能与 cosine distance 直接比较；Phase 9
融合时必须使用统一的排名 / 归一化契约。
"""

from __future__ import annotations

import math
import re
import unicodedata
from collections import Counter
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

#: 保留 ASCII 字母、数字及测试知识中常见的连接符；``v1.3``、``BUG-1234`` 不会被拆碎。
_ASCII_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")


@dataclass(frozen=True)
class LexicalDocument:
    """参与词法检索的最小文档；评测与生产检索共享。"""

    source_type: str
    source_id: str
    content: str


@dataclass(frozen=True)
class LexicalHit:
    """一条 BM25 命中及可解释的命中词项。"""

    document: LexicalDocument
    score: float
    document_index: int
    matched_terms: list[str] = field(default_factory=list)
    term_scores: dict[str, float] = field(default_factory=dict)


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


def tokenize(text: str) -> list[str]:
    """把混合中英文正文切成稳定、可复现的词项序列。

    标点与空白是分隔符，不进入索引；中文片段按字符生成 unigram 与 bigram，
    ASCII 片段保留完整标识符。返回顺序与正文出现顺序一致，便于解释命中。
    """
    normalized = _normalize(text)
    tokens: list[str] = []
    for match in re.finditer(
        r"[a-z0-9]+(?:[-_.][a-z0-9]+)*|[\u3400-\u4dbf\u4e00-\u9fff]+",
        normalized,
    ):
        value = match.group(0)
        if _ASCII_TOKEN.fullmatch(value):
            tokens.append(value)
            continue

        # 中文连续串：unigram 后跟 bigram。这样“支付超时”既能匹配“超时”，
        # 也能保留“支付”这个更强的双字信号。
        for char in value:
            tokens.append(char)
        for index in range(len(value) - 1):
            tokens.append(value[index : index + 2])
    return tokens


class InvertedIndex:
    """词项到文档的倒排表，记录词频与文档频率。"""

    def __init__(self, documents: Sequence[LexicalDocument]) -> None:
        self.documents = list(documents)
        self.term_frequencies: list[Counter[str]] = []
        self.document_lengths: list[int] = []
        postings: dict[str, dict[int, int]] = {}

        for document_index, document in enumerate(self.documents):
            frequencies = Counter(tokenize(document.content))
            self.term_frequencies.append(frequencies)
            self.document_lengths.append(sum(frequencies.values()))
            for term, frequency in frequencies.items():
                postings.setdefault(term, {})[document_index] = frequency

        self.postings = postings
        self.document_frequencies = {
            term: len(posting) for term, posting in self.postings.items()
        }

    @property
    def document_count(self) -> int:
        return len(self.documents)

    @property
    def average_document_length(self) -> float:
        if not self.document_lengths:
            return 0.0
        return sum(self.document_lengths) / len(self.document_lengths)

    def documents_for(self, term: str) -> dict[int, int]:
        """返回 ``{document_index: term_frequency}``；未知词项返回空表。

        参数按 Query 文本处理：``BUG-1234`` 会先走与文档相同的分词，而不是
        假设调用方已经手工切好单个 token。
        """
        frequencies: Counter[int] = Counter()
        for token in dict.fromkeys(tokenize(term)):
            for document_index, frequency in self.postings.get(token, {}).items():
                frequencies[document_index] += frequency
        return dict(frequencies)


class BM25Index:
    """Okapi BM25 检索。

    公式：``idf(t) * tf*(k1+1) / (tf + k1*(1-b+b*dl/avgdl))``，
    其中 ``idf = log(1 + (N-df+0.5)/(df+0.5))``，保证常见词也有非负权重。
    """

    def __init__(
        self,
        documents: Sequence[LexicalDocument],
        *,
        k1: float = 1.2,
        b: float = 0.75,
    ) -> None:
        if k1 <= 0:
            raise ValueError("k1 必须大于 0")
        if not 0 <= b <= 1:
            raise ValueError("b 必须在 [0, 1] 内")
        self.index = InvertedIndex(documents)
        self.k1 = k1
        self.b = b

    @property
    def documents(self) -> list[LexicalDocument]:
        return self.index.documents

    def idf(self, term: str) -> float:
        """词项逆文档频率；未出现词项按 ``df=0`` 计算。"""
        normalized = _normalize(term).strip()
        if normalized not in self.index.document_frequencies:
            # 允许调用方传“支付超时”这样的 Query 文本，按分词后的词项累加 IDF；
            # 若传的是完整查询串，不能返回一个看似有意义的单 term IDF。
            tokens = list(dict.fromkeys(tokenize(term)))
            if len(tokens) > 1:
                return sum(self.idf(token) for token in tokens)
        document_count = self.index.document_count
        document_frequency = self.index.document_frequencies.get(normalized, 0)
        return math.log(
            1 + (document_count - document_frequency + 0.5) / (document_frequency + 0.5)
        )

    def search(self, query: str, *, limit: int = 10) -> list[LexicalHit]:
        """返回得分大于 0 的命中，按分数降序、来源编号升序稳定排序。"""
        if limit < 1:
            raise ValueError("limit 必须大于 0")
        query_terms = list(dict.fromkeys(tokenize(query)))
        if not query_terms or not self.index.documents:
            return []

        scores: dict[int, float] = {}
        matched: dict[int, list[str]] = {}
        term_scores: dict[int, dict[str, float]] = {}
        average_length = self.index.average_document_length or 1.0

        for term in query_terms:
            posting = self.index.postings.get(term)
            if not posting:
                continue
            idf = self.idf(term)
            for document_index, frequency in posting.items():
                document_length = self.index.document_lengths[document_index]
                denominator = frequency + self.k1 * (
                    1 - self.b + self.b * document_length / average_length
                )
                contribution = idf * frequency * (self.k1 + 1) / denominator
                if contribution <= 0:
                    continue
                scores[document_index] = scores.get(document_index, 0.0) + contribution
                matched.setdefault(document_index, []).append(term)
                term_scores.setdefault(document_index, {})[term] = contribution

        ordered = sorted(
            scores,
            key=lambda document_index: (
                -scores[document_index],
                self.index.documents[document_index].source_type,
                self.index.documents[document_index].source_id,
            ),
        )
        return [
            LexicalHit(
                document=self.index.documents[document_index],
                score=scores[document_index],
                document_index=document_index,
                matched_terms=matched[document_index],
                term_scores=term_scores[document_index],
            )
            for document_index in ordered[:limit]
        ]


def tokenize_many(texts: Iterable[str]) -> list[list[str]]:
    """批量分词便捷入口，保留输入顺序。"""
    return [tokenize(text) for text in texts]


__all__ = [
    "BM25Index",
    "InvertedIndex",
    "LexicalDocument",
    "LexicalHit",
    "tokenize",
    "tokenize_many",
]
