from __future__ import annotations

import json
import subprocess
import sys
import unittest


class VisibleContractTest(unittest.TestCase):
    def test_public_request_has_operational_shape(self) -> None:
        proc = subprocess.run(
            [sys.executable, "-m", "src.cli", "--artifact-root", "data", "--request", "data/query_facets.json"],
            text=True, capture_output=True, timeout=90, check=False,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        payload = json.loads(proc.stdout.strip().splitlines()[-1])
        self.assertEqual(len(payload["results"]), 12)
        self.assertEqual(len(payload["pmcids"]), 12)
        self.assertTrue(all(set(item) == {"pmcid", "source", "score"} for item in payload["results"]))
        self.assertTrue(all(item["source"] in {"cardiac", "vascular", "systemic"} for item in payload["results"]))


if __name__ == "__main__":
    unittest.main()
