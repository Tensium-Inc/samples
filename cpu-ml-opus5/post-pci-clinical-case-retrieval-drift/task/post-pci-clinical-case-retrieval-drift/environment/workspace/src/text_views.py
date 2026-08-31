"""Text normalization and document assembly for the deployed candidate."""
from __future__ import annotations

import json
import re
import unicodedata
from typing import Any


def normalize(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return re.sub(r"\s+", " ", text).strip()


def document_text(row: dict[str, Any]) -> str:
    return normalize(" ".join([
        str(row.get("title", "")), str(row.get("case_summary", "")),
        str(row.get("final_diagnosis", "")), json.dumps(row.get("domain_hits", {}), sort_keys=True),
    ]))
