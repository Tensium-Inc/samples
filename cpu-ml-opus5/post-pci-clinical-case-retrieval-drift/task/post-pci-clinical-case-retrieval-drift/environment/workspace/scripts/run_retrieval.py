#!/usr/bin/env python3
"""Convenience wrapper for the public incident request."""
from __future__ import annotations

import subprocess
import sys


raise SystemExit(subprocess.call([
    sys.executable, "-m", "src.cli", "--artifact-root", "data",
    "--request", "data/query_facets.json",
]))
