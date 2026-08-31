"""Configuration loading."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict

import yaml

ROOT = Path(os.environ.get("WORKSPACE_DIR", Path(__file__).resolve().parent.parent))


def load_config(path: str | Path | None = None) -> Dict[str, Any]:
    p = Path(path) if path else ROOT / "config.yaml"
    with open(p) as f:
        return yaml.safe_load(f)


def resolve(rel: str | Path) -> Path:
    """Resolve a config-relative path against the pipeline root."""
    rel = Path(rel)
    return rel if rel.is_absolute() else ROOT / rel
