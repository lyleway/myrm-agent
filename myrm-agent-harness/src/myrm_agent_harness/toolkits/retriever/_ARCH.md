# retriever/

## Overview

Retrieval primitives (embed, BM25, fusion, rerank, split, sufficiency guard) that memory, wiki and skill search compose. Framework-agnostic; no business-layer imports.

Detailed design: [RETRIEVER_SYSTEM.md](RETRIEVER_SYSTEM.md)

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| `__init__.py` | Package | Lazy public exports for the retrieval entrypoints | ✅ |
| `bm25_retrieval.py` | Core | BM25 sparse retrieval; in-memory inverted index over document chunks | ✅ |
| `cjk_tokenizer.py` | Core | Single-pass position-preserving CJK unigram/bigram & ASCII word tokenizer, expansion budget protection, and CjkFtsQueryPlanner for two-tier FTS5 query synthesis | ✅ |
| `fusion_strategies.py` | Core | Score fusion utilities (weighted RRF, deterministic RRF with tie-breaking & recall debug) | ✅ |

| Submodule | Description |
|-----------|-------------|
| `bm25/` | BM25 algorithm and tokenization |
| `embedding/` | Local/cloud embedding services |
| `reranker/` | Post-retrieval reranking |
| `splitter/` | Document chunking strategies |
| `sufficiency/` | RSG post-retrieval sufficiency guard — [sufficiency/_ARCH.md](sufficiency/_ARCH.md) |

## Key Dependencies

- `utils`
- Optional `[retrieval]`: langchain-text-splitters, numpy, jieba, rank-bm25, tenacity
