"""Ranking implementation for the deployed candidate."""
from __future__ import annotations

from collections import Counter
from typing import Any

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import linear_kernel

from .text_views import document_text, normalize


def rank_cases(rows: list[dict[str, Any]], request: dict[str, Any]) -> dict[str, Any]:
    facets = request["facets"]
    query = normalize(" ".join(str(v) for v in facets.values()))
    documents = [document_text(row) for row in rows]

    vectorizer = TfidfVectorizer(strip_accents="unicode", ngram_range=(1, 1), min_df=1)
    matrix = vectorizer.fit_transform(documents + [query])
    scores = linear_kernel(matrix[-1], matrix[:-1]).ravel()
    order = np.lexsort((np.array([str(r["pmcid"]) for r in rows]), -scores))
    top_k = int(request.get("top_k", 12))
    chosen = order[:top_k]
    results = [
        {
            "pmcid": str(rows[i]["pmcid"]),
            "source": str(rows[i]["source"]),
            "score": round(float(scores[i]), 12),
        }
        for i in chosen
    ]
    domain_counts = Counter(str(r["source"]) for r in rows)
    return {
        "corpus_count": len(rows),
        "domain_counts": dict(sorted(domain_counts.items())),
        "results": results,
        "pmcids": [r["pmcid"] for r in results],
        "method": "global_word_unigram_cosine",
    }
