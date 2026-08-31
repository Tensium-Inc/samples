from __future__ import annotations

import sys
import unittest
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src import enrich, features, serve

SAMPLE = 4000


class EnrichmentContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.trips = pd.read_parquet(ROOT / "data" / "trips.parquet").head(SAMPLE)
        cls.zones, cls.rates = serve.load_reference()
        cls.out = enrich.enrich(cls.trips, cls.zones, cls.rates)

    def test_row_count_preserved(self) -> None:
        self.assertEqual(len(self.out), len(self.trips))

    def test_input_order_preserved(self) -> None:
        self.assertTrue(
            (self.out["PULocationID"].to_numpy() == self.trips["PULocationID"].to_numpy()).all())

    def test_all_enriched_columns_present(self) -> None:
        for column in enrich.ENRICHED_COLUMNS:
            self.assertIn(column, self.out.columns)

    def test_enriched_columns_are_strings(self) -> None:
        for column in enrich.ENRICHED_COLUMNS:
            self.assertTrue(self.out[column].map(lambda v: isinstance(v, str)).all(), column)

    def test_no_missing_enriched_values(self) -> None:
        self.assertFalse(self.out[enrich.ENRICHED_COLUMNS].isna().any().any())

    def test_no_empty_enriched_values(self) -> None:
        for column in enrich.ENRICHED_COLUMNS:
            self.assertFalse((self.out[column].str.strip() == "").any(), column)

    def test_feature_matrix_shape(self) -> None:
        matrix = features.build(self.trips, self.zones, self.rates)
        self.assertEqual(len(matrix), len(self.trips))
        self.assertEqual(list(matrix.columns), features.CATEGORICAL + features.NUMERIC)

    def test_feature_matrix_numeric_columns_finite(self) -> None:
        matrix = features.build(self.trips, self.zones, self.rates)
        for column in features.NUMERIC:
            self.assertTrue(pd.to_numeric(matrix[column], errors="coerce").notna().all(), column)

    def test_target_is_positive(self) -> None:
        y = features.target(self.trips)
        self.assertTrue((y > 0).all())

    def test_enrichment_is_deterministic(self) -> None:
        again = enrich.enrich(self.trips, self.zones, self.rates)
        pd.testing.assert_frame_equal(
            self.out[enrich.ENRICHED_COLUMNS], again[enrich.ENRICHED_COLUMNS])

    def test_time_contract_shape(self) -> None:
        from src import time_contract
        derived = time_contract.derive(self.trips["lpep_pickup_datetime"])
        self.assertEqual(list(derived.columns),
                         ["pickup_hour", "pickup_dayofweek", "time_quality"])
        self.assertEqual(len(derived), len(self.trips))

    def test_time_contract_has_no_missing_values(self) -> None:
        from src import time_contract
        derived = time_contract.derive(self.trips["lpep_pickup_datetime"])
        self.assertFalse(derived.isna().any().any())


if __name__ == "__main__":
    unittest.main()
