"""Smoke tests for the feature pipeline.

    python3 -m unittest discover -s tests -v

These check that the featurizer produces a well-formed, deterministic, integer feature
frame over the evaluation grid. They are structural: they do not assert a metric value,
because the backtest metric moves whenever the panel or the window changes.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import featurize, panel  # noqa: E402


class FeatureFrameTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.tx = pd.read_parquet(ROOT / "data" / "transactions.parquet")
        cls.probes = panel.probe_grid(cls.tx, "2011-03-01", "2011-05-01")
        cls._tmp = tempfile.TemporaryDirectory()
        cls.features = featurize.build_features(cls.tx, cls.probes, cache_dir=cls._tmp.name)

    @classmethod
    def tearDownClass(cls) -> None:
        cls._tmp.cleanup()

    def test_one_row_per_probe(self) -> None:
        self.assertEqual(len(self.features), len(self.probes))
        self.assertEqual(
            list(self.features["customer_id"]), list(self.probes["customer_id"]))

    def test_expected_columns_present(self) -> None:
        for col in featurize.FEATURE_COLUMNS:
            self.assertIn(col, self.features.columns)

    def test_all_features_integer(self) -> None:
        for col in featurize.FEATURE_COLUMNS:
            self.assertEqual(self.features[col].dtype.kind, "i", f"{col} is not integer")

    def test_no_missing_values(self) -> None:
        self.assertFalse(self.features[featurize.FEATURE_COLUMNS].isna().any().any())

    def test_distinct_skus_never_exceed_lines(self) -> None:
        self.assertTrue((self.features["n_sku"] <= self.features["n_lines"]).all())

    def test_credits_never_exceed_lines(self) -> None:
        self.assertTrue((self.features["n_credit"] <= self.features["n_lines"]).all())

    def test_recency_is_sentinel_or_non_negative(self) -> None:
        self.assertTrue((self.features["days_since_last"] >= -1).all())

    def test_inactive_rows_use_the_sentinel(self) -> None:
        idle = self.features[self.features["n_lines"] == 0]
        self.assertTrue((idle["days_since_last"] == -1).all())

    def test_deterministic_across_runs(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            again = featurize.build_features(self.tx, self.probes, cache_dir=d)
        pd.testing.assert_frame_equal(self.features, again)

    def test_labels_are_binary(self) -> None:
        labels = featurize.build_labels(self.tx, self.probes)
        self.assertEqual(len(labels), len(self.probes))
        self.assertTrue(set(labels.unique()).issubset({0, 1}))


if __name__ == "__main__":
    unittest.main()
