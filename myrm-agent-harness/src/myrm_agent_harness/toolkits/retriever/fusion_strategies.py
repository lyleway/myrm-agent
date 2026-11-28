"""Fusion strategy module.

Provides multi-query result fusion algorithms (RRF, etc.).

[INPUT]
- (none - standard library defaultdict)

[OUTPUT]
- fuse_rrf_deterministic: Deterministic Reciprocal Rank Fusion with multi-tier tie-breaking and adaptive normalization
- rrf_fusion: Reciprocal Rank Fusion algorithm for merging ranked result lists with deterministic tie-breaking
- SourceRank, SourceDebugStats, RecallDebug, RankedList, FusedHit: Structured provenance and debug tracking data types

[POS]
Score-fusion utilities for hybrid retrieval. Merges multiple ranked lists into a single
ranking using rank-based (not score-based) fusion.

"""

from __future__ import annotations

import logging
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class SourceRank:
    """Record of an item's hit source and 1-based rank within that source."""

    source: str
    rank: int


@dataclass(frozen=True, slots=True)
class SourceDebugStats:
    """Per-source debug latency and hit count."""

    source: str
    count: int
    latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class RecallDebug:
    """Structured recall debugging stats for transparency and troubleshooting."""

    per_source: list[SourceDebugStats] = field(default_factory=list)
    fused_count: int = 0


@dataclass(frozen=True, slots=True)
class RankedList[T]:
    """A ranked list of candidate items from a single retrieval source."""

    source: str
    items: list[T]
    weight: float = 1.0
    latency_ms: float | None = None


@dataclass(frozen=True, slots=True)
class FusedHit[T]:
    """A fused result item with normalized score and provenance tracking."""

    item: T
    score: float
    raw_rrf_score: float
    hit_by: list[SourceRank] = field(default_factory=list)


def fuse_rrf_deterministic[T](
    lists: list[RankedList[T]],
    *,
    key_func: Callable[[T], str],
    k: int = 60,
    top_k: int | None = 20,
    normalize_target: float = 0.98,
) -> tuple[list[FusedHit[T]], RecallDebug]:
    """Deterministic Reciprocal Rank Fusion (RRF) with multi-tier tie-breaking and adaptive normalization.

    Tie-breaking hierarchy:
    1. Highest normalized RRF score descending
    2. Multi-source consensus count descending (len(hit_by))
    3. Deterministic entity ID ascending (key_func(item)) to prevent hash/dict iteration drift.

    Score normalization:
    Maps raw reciprocal rank values (~0.016) to [0.0, 1.0] by dividing by theoretical maximum RRF
    for the active non-empty sources. This guarantees compatibility with downstream min_score threshold filters.
    """
    if not lists:
        return [], RecallDebug(per_source=[], fused_count=0)

    active_lists = [r_list for r_list in lists if r_list.items]
    if not active_lists:
        per_source = [SourceDebugStats(source=r_list.source, count=0, latency_ms=r_list.latency_ms) for r_list in lists]
        return [], RecallDebug(per_source=per_source, fused_count=0)

    # Accumulate scores and hits
    item_map: dict[str, T] = {}
    score_map: defaultdict[str, float] = defaultdict(float)
    hit_by_map: defaultdict[str, list[SourceRank]] = defaultdict(list)

    # Calculate theoretical maximum possible RRF for active sources at rank 1
    max_theoretical_rrf = sum((r_list.weight / (k + 1)) for r_list in active_lists)
    if max_theoretical_rrf <= 0:
        max_theoretical_rrf = 1.0 / (k + 1)

    for r_list in active_lists:
        weight = max(0.0, r_list.weight)
        for idx, item in enumerate(r_list.items):
            rank = idx + 1
            key = key_func(item)
            if key not in item_map:
                item_map[key] = item
            score_contribution = weight / (k + rank)
            score_map[key] += score_contribution
            hit_by_map[key].append(SourceRank(source=r_list.source, rank=rank))

    fused_candidates: list[tuple[str, float, float, list[SourceRank]]] = []
    for key in item_map:
        raw_score = score_map[key]
        normalized_score = min(1.0, (raw_score / max_theoretical_rrf) * normalize_target)
        fused_candidates.append((key, normalized_score, raw_score, hit_by_map[key]))

    # Deterministic sorting:
    # 1. -score (descending)
    # 2. -len(hit_by) (more sources agreeing is better)
    # 3. key (alphabetical ascending for 100% stable tie-break)
    fused_candidates.sort(key=lambda x: (-x[1], -len(x[3]), x[0]))

    if top_k is not None:
        fused_candidates = fused_candidates[:top_k]

    fused_hits: list[FusedHit[T]] = [
        FusedHit(
            item=item_map[key],
            score=norm_score,
            raw_rrf_score=raw_score,
            hit_by=hits,
        )
        for key, norm_score, raw_score, hits in fused_candidates
    ]

    recall_debug = RecallDebug(
        per_source=[
            SourceDebugStats(source=r_list.source, count=len(r_list.items), latency_ms=r_list.latency_ms)
            for r_list in lists
        ],
        fused_count=len(fused_hits),
    )

    return fused_hits, recall_debug


def rrf_fusion[T](
    query_results: list[list[tuple[T, float]]],
    k: int = 60,
    top_k: int | None = None,
    weights: list[float] | None = None,
) -> list[tuple[T, float]]:
    """Reciprocal Rank Fusion — merges multiple query results into a single ranking.

    A concise and efficient multi-query fusion strategy based on rank positions
    rather than absolute scores. Supports optional list-level weighting.

    Formula:
        RRF(doc) = Σ(w_i / (k + rank_i + 1))

    Where:
        - k: Constant parameter (typically 60), controls rank decay speed
        - rank_i: Document rank in the i-th query result (0-based)
        - w_i: Optional weight for the i-th query result list (defaults to 1.0)

    Characteristics:
        - More sensitive to rank positions than absolute scores
        - Does not perform any filtering — caller is responsible
        - Widely validated in practice

    Note:
        - This function does not filter zero/negative scores (single responsibility)
        - If filtering is needed, the caller should handle it before passing results in

    Args:
        query_results: Retrieval results per query, formatted as [(doc_idx, score), ...]
        k: RRF parameter controlling rank decay speed (default 60)
        top_k: Number of top results to return (None returns all)
        weights: Optional weight multiplier per query list (matches length of query_results)

    Returns:
        Fused result list [(doc_idx, rrf_score), ...] sorted by score descending

    Examples:
        >>> results = [
        ...     [(0, 0.9), (1, 0.5)],  # query1 results
        ...     [(1, 0.8), (0, 0.6)],  # query2 results
        ... ]
        >>> fused = rrf_fusion(results, k=60)

        >>> # Filter zero scores before passing in
        >>> filtered_results = [
        ...     [(idx, score) for idx, score in r if score > 0]
        ...     for r in results
        ... ]
        >>> fused = rrf_fusion(filtered_results, k=60)
    """
    rrf_scores: defaultdict[T, float] = defaultdict(float)
    hit_counts: defaultdict[T, int] = defaultdict(int)

    for i, results in enumerate(query_results):
        weight = weights[i] if weights is not None and i < len(weights) else 1.0
        if weight <= 0.0:
            continue
        for rank, (doc_idx, _) in enumerate(results):
            rrf_scores[doc_idx] += weight / (k + rank + 1)
            hit_counts[doc_idx] += 1

    # Deterministic multi-tier sort:
    # 1. -round(score, 9) (descending with epsilon bucket)
    # 2. -hit_count (descending consensus count)
    # 3. str(doc_idx) (ascending deterministic tie-breaker)
    sorted_items = sorted(
        rrf_scores.keys(),
        key=lambda doc: (-round(rrf_scores[doc], 9), -hit_counts[doc], str(doc)),
    )
    sorted_results = [(doc, rrf_scores[doc]) for doc in sorted_items]

    logger.debug(f"RRF fusion: {len(query_results)} queries -> {len(rrf_scores)} unique docs, selecting top_{top_k}")

    return sorted_results[:top_k] if top_k is not None else sorted_results
