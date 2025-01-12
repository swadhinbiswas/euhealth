"""Eurostat ingestion built on the verified dataset registry.

Design rules enforced here:

* One country per request. A multi-valued ``geo=`` returns ``200 OK`` with
  zero values, so batching countries silently loses data.
* No multi-value dimension filters for the same reason.
* Failures are visible. A response that decodes to zero rows is recorded as
  ``EMPTY`` in the ingestion log rather than passing as success.

The empty-result case was the original defect: 54 payloads logged ``OK``
while containing no observations at all.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from ingestion.http_client import fetch_json
from ingestion.jsonstat import flatten_jsonstat
from ingestion.registry import DATASETS, EU27, Dataset


def _url(dataset: str, params: dict[str, str]) -> str:
    from config import EUROSTAT_BASE

    query = "&".join(f"{k}={v}" for k, v in params.items() if v)
    return f"{EUROSTAT_BASE}{dataset}?{query}&lang=EN&format=JSON"


def pull_one(
    dataset: Dataset,
    params: dict[str, str],
    cache_name: str,
) -> pd.DataFrame:
    """Fetch and flatten a single Eurostat request.

    Returns an empty frame when the source is unreachable or yields no
    observations; the reason is written to the ingestion log either way.
    """
    url = _url(dataset.code, params)
    payload = fetch_json(url, source="eurostat", name=cache_name)

    if payload is None:
        return pd.DataFrame()

    rows = flatten_jsonstat(payload)
    if not rows:
        from ingestion.http_client import log_jsonl

        log_jsonl(
            "ingestion.jsonl",
            {
                "source": "eurostat",
                "status": "EMPTY",
                "url": url[:300],
                "bytes": 0,
                "note": f"{dataset.code}: 200 OK but no observations decoded",
            },
        )
        return pd.DataFrame()

    frame = pd.DataFrame(rows)
    frame["dataset_code"] = dataset.code
    frame["dataset_label"] = dataset.label
    return frame


def pull_per_country(
    dataset: Dataset,
    extra: dict[str, str] | None = None,
    geos: list[str] | None = None,
    since: int | None = None,
) -> pd.DataFrame:
    """Loop one country per request and concatenate the observations."""
    geos = geos or EU27
    start = since if since is not None else dataset.since

    frames: list[pd.DataFrame] = []
    for geo in geos:
        params: dict[str, str] = {"geo": geo}
        if start:
            params["sinceTimePeriod"] = str(start)
        if extra:
            params.update(extra)
        frame = pull_one(dataset, params, f"{dataset.code}_{geo}.json")
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, ignore_index=True)
    return out[out["geo"].isin(geos)].reset_index(drop=True)


# --- Workforce ---------------------------------------------------------------

def load_physicians() -> pd.DataFrame:
    """Physicians by age and sex, per country."""
    return pull_per_country(DATASETS["physicians"])


def load_nurses() -> pd.DataFrame:
    """Nurses and midwives by age and sex, per country."""
    return pull_per_country(DATASETS["nurses"])


def load_health_workforce() -> pd.DataFrame:
    """Physicians and nurses combined with a normalised profession column."""
    frames = []
    for key, profession in (("physicians", "Physician"), ("nurses", "Nurse")):
        frame = load_physicians() if key == "physicians" else load_nurses()
        if frame.empty:
            continue
        frame["profession"] = profession
        frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


# --- Specialisation, pipeline, migration -------------------------------------

def load_staff_specialty() -> pd.DataFrame:
    return pull_per_country(DATASETS["staff_specialty"])


def load_graduates() -> pd.DataFrame:
    return pull_per_country(DATASETS["graduates"])


def load_worker_migration() -> pd.DataFrame:
    return pull_per_country(DATASETS["worker_migration"])


# --- Capacity ----------------------------------------------------------------

def load_beds() -> pd.DataFrame:
    return pull_per_country(DATASETS["beds"])


def load_icu_beds() -> pd.DataFrame:
    return pull_per_country(DATASETS["icu_beds"])


def load_beds_ownership() -> pd.DataFrame:
    return pull_per_country(DATASETS["beds_ownership"])


# --- Demand, population, outcomes --------------------------------------------

def load_hospital_days() -> pd.DataFrame:
    """Hospital days by ICD-10 group. Very large; keep sinceTimePeriod tight."""
    return pull_per_country(DATASETS["hospital_days"])


def load_population_country() -> pd.DataFrame:
    return pull_per_country(DATASETS["population_country"])


def load_life_expectancy() -> pd.DataFrame:
    return pull_per_country(DATASETS["life_expectancy"])


def load_mortality() -> pd.DataFrame:
    return pull_per_country(DATASETS["mortality"])


def load_density() -> pd.DataFrame:
    return pull_per_country(DATASETS["density"])


def load_labour_status() -> pd.DataFrame:
    return pull_per_country(DATASETS["labour_status"])


def load_labour_education() -> pd.DataFrame:
    return pull_per_country(DATASETS["labour_education"])


def load_projections(
    geos: list[str] | None = None,
    last_time_period: int = 12,
) -> pd.DataFrame:
    """Population projections. Scenarios BSL/LFRT/LMRT drive demand scenarios."""
    dataset = DATASETS["projections"]
    frames = []
    for geo in (geos or EU27):
        params = {"geo": geo, "lastTimePeriod": str(last_time_period)}
        frame = pull_one(dataset, params, f"{dataset.code}_{geo}.json")
        if not frame.empty:
            frames.append(frame)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_population_nuts(geo_level: str = "NUTS2", time: str = "2023") -> pd.DataFrame:
    """Population by age and sex at NUTS level.

    ``demo_r_pjangrp3`` is queried with ``geoLevel=``, never ``geo=``, and
    returns all regions for one time point in a single request.
    """
    dataset = DATASETS["population_nuts"]
    params = {"geoLevel": geo_level, "time": time, "unit": "NR"}
    return pull_one(dataset, params, f"{dataset.code}_{geo_level}_{time}.json")


#: name -> loader, used by scripts/ingest_all.py
LOADERS: dict[str, Any] = {
    "health_workforce": load_health_workforce,
    "physicians": load_physicians,
    "nurses": load_nurses,
    "staff_specialty": load_staff_specialty,
    "graduates": load_graduates,
    "worker_migration": load_worker_migration,
    "beds": load_beds,
    "icu_beds": load_icu_beds,
    "beds_ownership": load_beds_ownership,
    "population_nuts2": lambda: load_population_nuts("NUTS2"),
    "population_nuts3": lambda: load_population_nuts("NUTS3"),
    "population_country": load_population_country,
    "life_expectancy": load_life_expectancy,
    "mortality": load_mortality,
    "density": load_density,
    "labour_status": load_labour_status,
    "labour_education": load_labour_education,
    "projections": load_projections,
    "hospital_days": load_hospital_days,
}