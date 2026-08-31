"""Case-record loading for the deployed candidate."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_cases(artifact_root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    cases_dir = artifact_root / "cases"
    for path in sorted(cases_dir.glob("*.jsonl")):
        inferred = path.name.split("_")[0].split("(")[0]
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            row["source"] = inferred
            rows.append(row)
    return rows


def load_request(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))
