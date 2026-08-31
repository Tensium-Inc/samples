#!/usr/bin/env python3
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd


def serve_payload(frame: pd.DataFrame) -> pd.DataFrame:
    out = frame.copy()
    indices = np.arange(len(out))
    for column, offset in (("PULocationID", 0), ("DOLocationID", 1), ("RatecodeID", 2)):
        values = out[column].astype("object")
        mask = (indices + offset) % 3 != 0
        values.loc[mask & values.notna()] = values.loc[mask & values.notna()].map(
            lambda value: f" {float(value):.1f} ")
        out[column] = values
    pickup = pd.to_datetime(out["lpep_pickup_datetime"])
    rendered = pickup.dt.strftime("%Y-%m-%dT%H:%M:%S").astype("object")
    ordinary = ~((pickup.dt.month == 11) & (pickup.dt.day == 3) & (pickup.dt.hour == 1))
    aware = ordinary & (indices % 3 != 0)
    localized = pickup[aware].dt.tz_localize("America/New_York", ambiguous=False,
                                            nonexistent="shift_forward")
    utc_mask = aware.copy()
    utc_mask[aware] = indices[aware] % 2 == 0
    rendered.loc[aware & ~utc_mask] = localized[~utc_mask[aware]].map(
        lambda value: value.isoformat())
    rendered.loc[utc_mask] = localized[utc_mask[aware]].dt.tz_convert("UTC").map(
        lambda value: value.isoformat().replace("+00:00", "Z"))
    out["lpep_pickup_datetime"] = rendered
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--candidate", required=True)
    ap.add_argument("--trips", required=True)
    ap.add_argument("--zones", required=True)
    ap.add_argument("--rates", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    candidate = Path(args.candidate)
    source = candidate / "src" / "enrich.py"
    spec = importlib.util.spec_from_file_location("candidate_enrich", source)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    trips = serve_payload(pd.read_parquet(args.trips))
    zones = pd.read_csv(args.zones)
    rates = pd.read_csv(args.rates)
    result = module.enrich(trips, zones, rates)
    time_source = candidate / "src" / "time_contract.py"
    time_spec = importlib.util.spec_from_file_location("candidate_time_contract", time_source)
    if time_spec is None or time_spec.loader is None:
        raise RuntimeError("time contract cannot be loaded")
    time_module = importlib.util.module_from_spec(time_spec)
    time_spec.loader.exec_module(time_module)
    temporal = time_module.derive(trips["lpep_pickup_datetime"])
    for column in temporal.columns:
        result[column] = temporal[column].astype(str)
    columns = ["pu_borough", "pu_service_zone", "do_borough", "do_service_zone",
               "rate_class", "fare_basis", "pickup_hour", "pickup_dayofweek", "time_quality"]
    result[columns].to_parquet(args.output, index=False)
    print(json.dumps({"rows": len(result)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
