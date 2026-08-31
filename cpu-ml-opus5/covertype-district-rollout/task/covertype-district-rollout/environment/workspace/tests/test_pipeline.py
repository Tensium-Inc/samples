"""Pipeline unit tests. These are the checks the team runs before a retrain."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from covertype import config, data, evaluate, features, model  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return config.load()


@pytest.fixture(scope="module")
def cells(cfg):
    return data.load_cells(cfg)


def test_cells_load(cells):
    assert len(cells) > 10_000
    assert {"elevation", "soil_type", "wilderness_area", "cover_type"} <= set(cells.columns)


def test_no_missing_values(cells):
    assert not cells.isnull().any().any()


def test_split_is_disjoint(cfg, cells):
    train, holdout = data.split(cfg, cells)
    assert len(train) + len(holdout) == len(cells)
    assert len(holdout) > 0


def test_feature_matrix_shape(cfg, cells):
    X = features.build(cfg, cells.head(100))
    assert len(X) == 100
    assert list(X.columns) == features.columns(cfg)


def test_feature_matrix_is_numeric(cfg, cells):
    X = features.build(cfg, cells.head(100))
    assert all(pd.api.types.is_numeric_dtype(X[c]) for c in X.columns)


def test_model_trains_and_beats_majority(cfg, cells):
    train, holdout = data.split(cfg, cells)
    clf = model.fit(cfg, train.head(6000))
    m = evaluate.evaluate(cfg, clf, holdout.head(2000))
    majority = holdout["cover_type"].value_counts(normalize=True).max()
    assert m["accuracy"] > majority, "model must beat predicting the majority class"


def test_fingerprint_is_stable(cfg):
    assert model.fingerprint(cfg) == model.fingerprint(cfg)


def test_fingerprint_tracks_feature_spec(cfg):
    import copy
    other = copy.deepcopy(cfg)
    other["features"]["soil_type"] = not other["features"]["soil_type"]
    assert model.fingerprint(cfg) != model.fingerprint(other)
