"""Cross-validation fold construction."""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
from sklearn.model_selection import KFold


def make_splits(
    df: pd.DataFrame, X: np.ndarray, y: np.ndarray, cfg: Dict[str, Any]
) -> List[Tuple[np.ndarray, np.ndarray]]:
    """Build the list of (train_idx, test_idx) pairs the evaluation averages over."""
    ev = cfg["evaluation"]
    splitter = KFold(
        n_splits=int(ev["n_splits"]),
        shuffle=bool(ev.get("shuffle", True)),
        random_state=int(ev["random_state"]) if ev.get("shuffle", True) else None,
    )
    return [(tr, te) for tr, te in splitter.split(X, y)]
