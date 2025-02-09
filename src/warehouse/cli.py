"""Build the gold warehouse end to end.

Usage:
    python -m scripts.build_warehouse
    python -m src.warehouse.cli --report
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from config import (  # noqa: E402
    REFERENCE_DOCTORS_PER_1000,
    WAREHOUSE,
)
from ingestion import eurostat  # noqa: E402
from ingestion.registry import EU27  # noqa: E402
from quality.dq import assess_workforce, write_report  # noqa: E402
from warehouse.dims import (  # noqa: E402
    ALL_DIMENSIONS,
    dim_date,
    dim_region,
)
from warehouse.facts import (  # noqa: E402
    build_warehouse,
    fact_healthcare_workers,
    fact_hospital_capacity,
    fact_population,
    fact_population_country,
    fact_population_nuts,
    fact_retirement,
    fact_staffing_shortage,
)


def build(report_only: bool = False) -> dict:
    print("loading sources ...", flush=True)
    workforce = eurostat.load_health_workforce()
    phys = eurostat.load_physicians()
    nurses = eurostat.load_nurses()
    population = eurostat.load_population_country()
    pop_total = eurostat.load_population_total()
    nuts_pop = eurostat.load_population_nuts("NUTS2")
    beds = eurostat.load_beds()
    icu = eurostat.load_icu_beds()

    # --- data quality gate ---------------------------------------------------
    reports = []
    for frame, profession, label in (
        (phys, "phys", "physicians"), (nurses, "nurse", "nurses")
    ):
        if frame.empty:
            continue
        frame = frame.copy()
        frame["profession"] = frame["dataset_code"].map(
            {"hlth_rs_phys": "Physician", "hlth_rs_nurse": "Nurse"}
        )
        report = assess_workforce(frame, profession, set(EU27))
        reports.append(report)
        out = ROOT / "data" / "logs" / f"dq_{label}.json"
        write_report(report, out)
        print(
            f"  DQ {label:12s} score={report.score:6.2f} "
            f"failures={len(report.blocking_failures)}",
            flush=True,
        )

    if report_only:
        return {"quality": [r.to_dict() for r in reports]}

    # --- dimensions ----------------------------------------------------------
    nuts_codes = (
        nuts_pop["geo"].dropna() if not nuts_pop.empty else pd.Series(dtype=str)
    )
    dimensions = {
        name: fn() for name, fn in ALL_DIMENSIONS.items()
    }
    dimensions["dim_region"] = dim_region(nuts_codes, "NUTS2")
    dimensions["dim_date"] = dim_date(range(2000, 2036))

    # --- facts ---------------------------------------------------------------
    built = {
        "fact_healthcare_workers": fact_healthcare_workers(workforce),
        "fact_retirement": fact_retirement(workforce),
        "fact_population_indicators": fact_population_country(population),
        "fact_population": fact_population(pop_total),
        "fact_population_nuts": fact_population_nuts(nuts_pop),
        "fact_hospital_capacity": fact_hospital_capacity(beds, icu),
    }

    # Shortage needs a population denominator; use total headcount.
    built["fact_staffing_shortage"] = fact_staffing_shortage(
        workforce, built["fact_population"],
        REFERENCE_DOCTORS_PER_1000,
    )

    counts = build_warehouse(built, WAREHOUSE, dimensions)

    print("\nwarehouse written:", WAREHOUSE)
    for name, n in sorted(counts.items()):
        print(f"  {name:32s} {n:9,d} rows")
    return counts


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true",
                        help="run quality checks only")
    args = parser.parse_args()
    result = build(report_only=args.report)
    if args.report:
        print(json.dumps(result, indent=2)[:2000])