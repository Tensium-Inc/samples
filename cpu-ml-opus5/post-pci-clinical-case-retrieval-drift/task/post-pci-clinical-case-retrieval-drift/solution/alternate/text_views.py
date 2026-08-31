"""Serving-time eligible text views."""
from __future__ import annotations

import re
import unicodedata
from typing import Any


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(text)).lower()).strip()


def document_text(row: dict[str, Any]) -> str:
    return normalize(f"{row.get('title', '')}\n{row.get('case_summary', '')}")
