from __future__ import annotations

import numpy as np
import pandas as pd

UNKNOWN = "UNKNOWN"

ENRICHED_COLUMNS = ["pu_borough", "pu_service_zone", "do_borough",
                    "do_service_zone", "rate_class", "fare_basis"]

# Trips published with no rate code are, by the TLC rate-code dictionary, code 99
# ("unknown_reported" / "unspecified"). Absent rate codes are therefore resolved through
# the codebook like any other code, not dropped to UNKNOWN.
UNREPORTED_RATE_CODE = 99


def _numeric_key(values: pd.Series) -> pd.Series:
    coerced = pd.to_numeric(values, errors="coerce")
    integral = coerced.notna() & np.isclose(coerced % 1, 0.0)
    out = pd.Series(pd.NA, index=values.index, dtype="Int64")
    out[integral] = coerced[integral].astype("int64")
    return out


def _clean(series: pd.Series) -> pd.Series:
    text = series.astype("string").str.strip()
    return text.where(text.notna() & (text != "") & (text != "N/A"))


def _zone_attributes(zones: pd.DataFrame) -> pd.DataFrame:
    # Resolve each row as the table publishes it: a blank Borough or service_zone falls back to
    # that row's Zone label (this is how the reserved rows 264/265 carry their meaning). A field
    # still blank after fallback has no value and later resolves to UNKNOWN.
    table = zones.copy()
    table["LocationID"] = _numeric_key(table["LocationID"])
    table = table.dropna(subset=["LocationID"]).drop_duplicates(
        subset=["LocationID"], keep="first").set_index("LocationID")
    zone_label = _clean(table["Zone"])
    resolved = {column: _clean(table[column]).fillna(zone_label)
                for column in ("Borough", "service_zone")}
    return pd.DataFrame(resolved, index=table.index)


def _resolve_zone(ids: pd.Series, table: pd.DataFrame, column: str) -> pd.Series:
    key = _numeric_key(ids)
    mapped = key.map(table[column])
    return mapped.fillna(UNKNOWN).astype(str)


def _resolve_rate(codes: pd.Series, rates: pd.DataFrame) -> pd.DataFrame:
    table = rates.copy()
    table["code"] = _numeric_key(table["code"])
    table = table.dropna(subset=["code"]).drop_duplicates(
        subset=["code"], keep="first").set_index("code")
    key = _numeric_key(codes)
    key = key.mask(codes.isna(), UNREPORTED_RATE_CODE)  # absent code -> 99, not UNKNOWN
    out = {}
    for column in ("rate_class", "fare_basis"):
        mapped = key.map(table[column])
        out[column] = mapped.fillna(UNKNOWN).astype(str)
    return pd.DataFrame(out, index=codes.index)


def enrich(trips: pd.DataFrame, zones: pd.DataFrame, rates: pd.DataFrame) -> pd.DataFrame:
    zone_table = _zone_attributes(zones)
    out = trips.copy()
    out["pu_borough"] = _resolve_zone(trips["PULocationID"], zone_table, "Borough")
    out["pu_service_zone"] = _resolve_zone(trips["PULocationID"], zone_table, "service_zone")
    out["do_borough"] = _resolve_zone(trips["DOLocationID"], zone_table, "Borough")
    out["do_service_zone"] = _resolve_zone(trips["DOLocationID"], zone_table, "service_zone")
    rate_frame = _resolve_rate(trips["RatecodeID"], rates)
    out["rate_class"] = rate_frame["rate_class"]
    out["fare_basis"] = rate_frame["fare_basis"]
    return out
