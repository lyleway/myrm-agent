"""Retrieval toolkit — embedding, BM25, fusion, rerank and chunk splitting.

[INPUT]
- .bm25_retrieval::bm25_retrieval (POS: BM25 sparse search)
- .cjk_tokenizer::tokenize_cjk_bigram, build_cjk_* (POS: CJK tokenization for FTS and BM25)
- .embedding::EmbeddingService (POS: vector embedding service)
- .fusion_strategies::fuse_rrf_deterministic, RankedList, FusedHit, RecallDebug, SourceRank, SourceDebugStats (POS: rank fusion)
- .reranker::RerankerService (POS: reranking service)
- .splitter::TextChunker (POS: token-aware text splitter)

[OUTPUT]
- EmbeddingService, RerankerService, RerankerConfig, TextChunker, bm25_retrieval, CJK tokenizers, deterministic RRF fusion types

[POS]
Retrieval toolkit public facade re-exporting key entrypoints; every attribute is imported on first access.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from myrm_agent_harness.toolkits.retriever.bm25.tokenizer import preload_tokenizer
    from myrm_agent_harness.toolkits.retriever.bm25_retrieval import bm25_retrieval
    from myrm_agent_harness.toolkits.retriever.cjk_tokenizer import (
        build_cjk_index_segment,
        build_cjk_query_token_tiers,
        build_cjk_query_tokens,
        tokenize_cjk_bigram,
    )
    from myrm_agent_harness.toolkits.retriever.embedding import EmbeddingService, get_embedding_service
    from myrm_agent_harness.toolkits.retriever.fusion_strategies import (
        FusedHit,
        RankedList,
        RecallDebug,
        SourceDebugStats,
        SourceRank,
        fuse_rrf_deterministic,
    )
    from myrm_agent_harness.toolkits.retriever.reranker import RerankerConfig, RerankerService, get_reranker_service
    from myrm_agent_harness.toolkits.retriever.splitter import TextChunker

__all__ = [
    # Embedding service
    "EmbeddingService",
    # Reranker service
    "RerankerConfig",
    "RerankerService",
    # Text chunker
    "TextChunker",
    # BM25
    "bm25_retrieval",
    "get_embedding_service",
    "get_reranker_service",
    "preload_tokenizer",
    # CJK Tokenizer
    "tokenize_cjk_bigram",
    "build_cjk_index_segment",
    "build_cjk_query_tokens",
    "build_cjk_query_token_tiers",
    # Deterministic RRF Fusion
    "fuse_rrf_deterministic",
    "RankedList",
    "FusedHit",
    "RecallDebug",
    "SourceRank",
    "SourceDebugStats",
]

_LAZY_IMPORTS = {
    "fuse_rrf_deterministic": ("myrm_agent_harness.toolkits.retriever.fusion_strategies", "fuse_rrf_deterministic"),
    "RankedList": ("myrm_agent_harness.toolkits.retriever.fusion_strategies", "RankedList"),
    "FusedHit": ("myrm_agent_harness.toolkits.retriever.fusion_strategies", "FusedHit"),
    "RecallDebug": ("myrm_agent_harness.toolkits.retriever.fusion_strategies", "RecallDebug"),
    "SourceRank": ("myrm_agent_harness.toolkits.retriever.fusion_strategies", "SourceRank"),
    "SourceDebugStats": ("myrm_agent_harness.toolkits.retriever.fusion_strategies", "SourceDebugStats"),
    "tokenize_cjk_bigram": ("myrm_agent_harness.toolkits.retriever.cjk_tokenizer", "tokenize_cjk_bigram"),
    "build_cjk_index_segment": ("myrm_agent_harness.toolkits.retriever.cjk_tokenizer", "build_cjk_index_segment"),
    "build_cjk_query_tokens": ("myrm_agent_harness.toolkits.retriever.cjk_tokenizer", "build_cjk_query_tokens"),
    "build_cjk_query_token_tiers": (
        "myrm_agent_harness.toolkits.retriever.cjk_tokenizer",
        "build_cjk_query_token_tiers",
    ),
    "bm25_retrieval": ("myrm_agent_harness.toolkits.retriever.bm25_retrieval", "bm25_retrieval"),
    "preload_tokenizer": ("myrm_agent_harness.toolkits.retriever.bm25.tokenizer", "preload_tokenizer"),
    "EmbeddingService": ("myrm_agent_harness.toolkits.retriever.embedding", "EmbeddingService"),
    "get_embedding_service": ("myrm_agent_harness.toolkits.retriever.embedding", "get_embedding_service"),
    "RerankerConfig": ("myrm_agent_harness.toolkits.retriever.reranker", "RerankerConfig"),
    "RerankerService": ("myrm_agent_harness.toolkits.retriever.reranker", "RerankerService"),
    "get_reranker_service": ("myrm_agent_harness.toolkits.retriever.reranker", "get_reranker_service"),
    "TextChunker": ("myrm_agent_harness.toolkits.retriever.splitter", "TextChunker"),
}

if __debug__:
    _lazy_set = set(_LAZY_IMPORTS.keys())
    _all_set = set(__all__)
    _extra = _lazy_set - _all_set
    if _extra:
        raise RuntimeError(f"retriever: _LAZY_IMPORTS has symbols not in __all__: {_extra}")


def __getattr__(name: str) -> object:
    """Lazy load retriever components on first access."""
    if name in _LAZY_IMPORTS:
        from importlib import import_module

        module_path, attr_name = _LAZY_IMPORTS[name]
        module = import_module(module_path)
        value = getattr(module, attr_name)
        globals()[name] = value
        return value

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
