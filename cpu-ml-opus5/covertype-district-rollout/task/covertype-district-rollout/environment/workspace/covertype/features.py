"""Design-matrix construction.

Which column families are included is driven by `features:` in the config, so the same
code path builds the matrix at training time and at inference time. Keeping those two in
step matters: a model fitted on one column set cannot score a frame built with another.
"""
from __future__ import annotations

import pandas as pd

TERRAIN = [
    "elevation", "aspect", "slope",
    "horizontal_distance_to_hydrology", "vertical_distance_to_hydrology",
    "horizontal_distance_to_roadways",
    "hillshade_9am", "hillshade_noon", "hillshade_3pm",
    "horizontal_distance_to_fire_points",
]
SOIL_CODES = list(range(1, 41))
AREAS = ["rawah", "neota", "comanche_peak", "cache_la_poudre"]


def spec(cfg: dict) -> dict:
    f = cfg["features"]
    return {"terrain": bool(f["terrain"]),
            "soil_type": bool(f["soil_type"]),
            "wilderness_area": bool(f["wilderness_area"])}


def columns(cfg: dict) -> list[str]:
    sp, cols = spec(cfg), []
    if sp["terrain"]:
        cols += TERRAIN
    if sp["soil_type"]:
        cols += [f"soil_{s}" for s in SOIL_CODES]
    if sp["wilderness_area"]:
        cols += [f"area_{a}" for a in AREAS]
    return cols


def build(cfg: dict, df: pd.DataFrame) -> pd.DataFrame:
    sp = spec(cfg)
    out = pd.DataFrame(index=df.index)
    if sp["terrain"]:
        for c in TERRAIN:
            out[c] = df[c]
    if sp["soil_type"]:
        for s in SOIL_CODES:
            out[f"soil_{s}"] = (df["soil_type"] == s).astype("int8")
    if sp["wilderness_area"]:
        for a in AREAS:
            out[f"area_{a}"] = (df["wilderness_area"] == a).astype("int8")
    if out.shape[1] == 0:
        raise ValueError("feature spec selects no columns")
    return out[columns(cfg)]
