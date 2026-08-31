"""R10 per-domain, rank-only multi-view clinical retriever with request-bound panel allocation."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from .text_views import document_text, normalize

BASE_DOMAINS = ("cardiac", "systemic", "vascular")
RRF_OFFSET = 60


def _rank_positions(scores: np.ndarray, rows: list[dict[str, Any]]) -> dict[str, int]:
    order = sorted(range(len(rows)), key=lambda i: (-float(scores[i]), str(rows[i]["pmcid"])))
    return {str(rows[index]["pmcid"]): rank for rank, index in enumerate(order, start=1)}


def _domain_rank(rows: list[dict[str, Any]], query: str) -> list[tuple[dict[str, Any], float]]:
    docs = [document_text(row) for row in rows]
    query = normalize(query)
    views = [
        TfidfVectorizer(
            strip_accents="unicode", ngram_range=(1, 2), min_df=2, max_df=0.98,
            sublinear_tf=True, norm="l2",
        ),
        TfidfVectorizer(
            strip_accents="unicode", analyzer="char_wb", ngram_range=(3, 5), min_df=2,
            max_features=60000, sublinear_tf=True, norm="l2",
        ),
    ]
    ranks: list[dict[str, int]] = []
    for vectorizer in views:
        matrix = vectorizer.fit_transform(docs)
        scores = linear_kernel(vectorizer.transform([query]), matrix).ravel()
        ranks.append(_rank_positions(scores, rows))
    fused = []
    for row in rows:
        pmcid = str(row["pmcid"])
        value = sum(1.0 / (RRF_OFFSET + rank[pmcid]) for rank in ranks)
        fused.append((row, value))
    return sorted(fused, key=lambda item: (-item[1], str(item[0]["pmcid"])))


def _release_cycle(request: dict[str, Any]) -> tuple[str, ...]:
    query_id = normalize(request.get("query_id", ""))
    phase = sum(query_id.encode("utf-8")) % len(BASE_DOMAINS)
    if int(request.get("top_k", 12)) % 2:
        return BASE_DOMAINS[phase:] + BASE_DOMAINS[:phase]
    return tuple(BASE_DOMAINS[(phase - index) % len(BASE_DOMAINS)] for index in range(len(BASE_DOMAINS)))


def _weave(per_domain: dict[str, list[tuple[dict[str, Any], float]]], cycle: tuple[str, ...], top_k: int):
    if top_k < 1:
        raise ValueError("top_k must be positive")
    selected: list[tuple[dict[str, Any], float]] = []
    rank_index = 0
    while len(selected) < top_k:
        progressed = False
        for domain in cycle:
            ranked = per_domain[domain]
            if rank_index < len(ranked):
                selected.append(ranked[rank_index])
                progressed = True
                if len(selected) == top_k:
                    break
        if not progressed:
            break
        rank_index += 1
    if len(selected) != top_k:
        raise ValueError("top_k exceeds available release rows")
    return selected


def rank_cases(rows: list[dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
    facets = request["facets"]
    per_domain: dict[str, list[tuple[dict[str, Any], float]]] = {}
    for domain in BASE_DOMAINS:
        domain_rows = [row for row in rows if str(row["source"]) == domain]
        per_domain[domain] = _domain_rank(domain_rows, str(facets[domain]))
    top_k = int(request.get("top_k", 12))
    selected = _weave(per_domain, _release_cycle(request), top_k)
    results = [
        {"pmcid": str(row["pmcid"]), "source": str(row["source"]), "score": round(float(score), 12)}
        for row, score in selected
    ]
    counts = Counter(str(row["source"]) for row in rows)
    return {
        "corpus_count": len(rows), "domain_counts": dict(sorted(counts.items())),
        "results": results, "pmcids": [item["pmcid"] for item in results],
        "method": "release_rank_fusion",
    }
