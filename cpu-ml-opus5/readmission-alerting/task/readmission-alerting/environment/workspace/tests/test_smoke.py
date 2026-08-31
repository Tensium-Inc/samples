"""Smoke tests for the alerting pipeline.

    python3 -m unittest discover -s tests -v

These check the pipeline is wired together and produces a well-formed report. They do not
attempt to judge whether the published figure is the right quantity - only that the pipeline
runs and reports something coherent.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.config import load_config                              # noqa: E402
from src.evaluate import fit_final, predict_scores, run_evaluation  # noqa: E402
from src.load import load_encounters                            # noqa: E402


class TestPipeline(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.cfg = load_config()
        cls.df = load_encounters(cls.cfg)

    def test_table_loads(self) -> None:
        self.assertGreater(len(self.df), 10000)
        self.assertIn("readmitted_30d", self.df.columns)

    def test_evaluation_reports_a_usable_figure(self) -> None:
        r = run_evaluation(self.df, self.cfg)
        self.assertIn("score", r)
        self.assertGreater(r["score"], 0.0)
        self.assertLessEqual(r["score"], 1.0)
        self.assertEqual(len(r["fold_scores"]), r["n_splits"])

    def test_every_fold_is_populated(self) -> None:
        r = run_evaluation(self.df, self.cfg)
        self.assertTrue(all(s > 0.0 for s in r["fold_scores"]))

    def test_final_model_scores_every_encounter(self) -> None:
        model = fit_final(self.df, self.cfg)
        s = predict_scores(model, self.df, self.cfg)
        self.assertEqual(len(s), len(self.df))
        self.assertTrue(((s >= 0.0) & (s <= 1.0)).all())
