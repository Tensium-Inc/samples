"""Alternate correct implementation of the serving contract.

Structurally independent of solution/gold: plain Python dict lookups built row by row
from the CSVs and per-value resolution, rather than vectorized pandas joins. It must produce
byte-identical enrichment to the gold on the graded populations.
"""
from __future__ import annotations

import math

import pandas as pd

UNKNOWN = "UNKNOWN"

ENRICHED_COLUMNS = ["pu_borough", "pu_service_zone", "do_borough",
                    "do_service_zone", "rate_class", "fare_basis"]

UNREPORTED_RATE_CODE = 99


def _as_int(value) -> int | None:
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return None
    if math.isnan(number) or not number.is_integer():
        return None
    return int(number)


def _text_or_none(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if text == "" or text == "N/A" or text.lower() == "nan":
        return None
    return text


def _zone_maps(zones: pd.DataFrame):
    borough, service = {}, {}
    for record in zones.to_dict("records"):
        key = _as_int(record.get("LocationID"))
        if key is None or key in borough:
            continue
        zone_label = _text_or_none(record.get("Zone"))
        boro = _text_or_none(record.get("Borough"))
        svc = _text_or_none(record.get("service_zone"))
        borough[key] = boro if boro is not None else zone_label
        service[key] = svc if svc is not None else zone_label
    return borough, service


def _rate_maps(rates: pd.DataFrame):
    rate_class, fare_basis = {}, {}
    for record in rates.to_dict("records"):
        key = _as_int(record.get("code"))
        if key is None or key in rate_class:
            continue
        rate_class[key] = str(record.get("rate_class")).strip()
        fare_basis[key] = str(record.get("fare_basis")).strip()
    return rate_class, fare_basis


def enrich(trips: pd.DataFrame, zones: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    borough, service = _zone_maps(zones)
    rate_class, fare_basis = _rate_maps(rates)
    out = trips.copy()

    def resolve_zone(series, table):
        result = []
        for value in series:
            key = _as_int(value)
            hit = table.get(key) if key is not None else None
            result.append(hit if hit is not None else UNKNOWN)
        return result

    out["pu_borough"] = resolve_zone(trips["PULocationID"], borough)
    out["pu_service_zone"] = resolve_zone(trips["PULocationID"], service)
    out["do_borough"] = resolve_zone(trips["DOLocationID"], borough)
    out["do_service_zone"] = resolve_zone(trips["DOLocationID"], service)

    classes, bases = [], []
    for value in trips["RatecodeID"]:
        key = UNREPORTED_RATE_CODE if pd.isna(value) else _as_int(value)
        classes.append(rate_class.get(key, UNKNOWN))
        bases.append(fare_basis.get(key, UNKNOWN))
    out["rate_class"] = classes
    out["fare_basis"] = bases
    return out
