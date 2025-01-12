"""Ingest all verified Eurostat sources into the raw landing zone.

Every loader is registered in ``ingestion.eurostat.LOADERS``. A loader that
returns zero rows is reported as EMPTY so a silent regression cannot be
mistaken for success.
"""
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ingestion import eurostat  # noqa: E402

# Datasets pulled by default. hospital_days is very large and is opt-in.
DEFAULT_LOADERS = [
    "health_workforce",
    "staff_specialty",
    "graduates",
    "worker_migration",
    "beds",
    "icu_beds",
    "population_nuts2",
    "population_country",
    "life_expectancy",
    "mortality",
    "density",
    "labour_status",
    "projections",
]


def run(label, fn):
    t0 = time.time()
    try:
        df = fn()
        n = len(df)
        status = "OK" if n else "EMPTY"
        print(
            f"[{status:5s}] {label:22s} rows={n:8d} {time.time() - t0:6.1f}s",
            flush=True,
        )
        return df
    except Exception as exc:
        print(
            f"[FAIL ] {label:22s} {type(exc).__name__}: {str(exc)[:110]}",
            flush=True,
        )
        return None


if __name__ == "__main__":
    requested = sys.argv[1:] or DEFAULT_LOADERS
    for name in requested:
        loader = eurostat.LOADERS.get(name)
        if loader is None:
            print(f"[SKIP ] {name:22s} not a known loader", flush=True)
            continue
        run(name, loader)
    print("DONE", flush=True)