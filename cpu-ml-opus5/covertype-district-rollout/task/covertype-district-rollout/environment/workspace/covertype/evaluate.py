"""Scoring helpers used by the training script and the tests."""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, recall_score

from . import features, model

LABEL = "cover_type"


def evaluate(cfg: dict, clf, frame: pd.DataFrame) -> dict:
    X = features.build(cfg, frame)
    y = frame[LABEL]
    pred = clf.predict(X)
    classes = sorted(pd.unique(y))
    rec = recall_score(y, pred, labels=classes, average=None, zero_division=0)
    return {
        "accuracy": round(float(accuracy_score(y, pred)), 4),
        "macro_recall": round(float(np.mean(rec)), 4),
        "per_class_recall": {int(c): round(float(r), 4) for c, r in zip(classes, rec)},
        "n": int(len(frame)),
    }
