#!/usr/bin/env python
"""Install the geography needed for BI map visuals.

Copies verified NUTS 2021 boundaries from the raw landing zone into
``data/geo`` with predictable names that a BI tool can ingest directly.

Power BI's shape map consumes GeoJSON from a URL or a file, so the files are
kept flat and named by level. Run ``make ingest`` first so the payloads exist.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from config import USER_AGENT  # noqa: E402

GEO = ROOT / "data" / "geo"
GISCO = "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"

#: Filenames verified against GISCO. The 10M scale is the general-purpose
#: resolution; 03M and 60M are available for small multiples and country maps.
FILES = {
    "nuts_level0_10m.geojson": "NUTS_RG_10M_2021_3035_LEVL_0.geojson",
    "nuts_level1_10m.geojson": "NUTS_RG_10M_2021_3035_LEVL_1.geojson",
    "nuts_level2_10m.geojson": "NUTS_RG_10M_2021_3035_LEVL_2.geojson",
    "nuts_level3_10m.geojson": "NUTS_RG_10M_2021_3035_LEVL_3.geojson",
    "nuts_level2_60m.geojson": "NUTS_RG_60M_2021_3035_LEVL_2.geojson",
    "nuts_level0_60m.geojson": "NUTS_RG_60M_2021_4326_LEVL_0.geojson",
}


def install(force: bool = False) -> int:
    GEO.mkdir(parents=True, exist_ok=True)
    installed = 0
    for local, remote in FILES.items():
        target = GEO / local
        if target.exists() and not force:
            print(f"  cached  {local}")
            installed += 1
            continue
        url = f"{GISCO}{remote}"
        try:
            response = requests.get(
                url, headers={"User-Agent": USER_AGENT}, timeout=180
            )
            response.raise_for_status()
            payload = response.content
            temporary = target.with_suffix(target.suffix + ".part")
            temporary.write_bytes(payload)
            temporary.replace(target)
            print(f"  fetched {local}  ({len(payload):,} bytes)")
            installed += 1
        except Exception as exc:
            print(f"  FAILED  {local}: {type(exc).__name__}: {exc}")
    return installed


if __name__ == "__main__":
    count = install(force="--force" in sys.argv)
    print(f"\n{count}/{len(FILES)} boundary files available in data/geo")
    raise SystemExit(0 if count else 1)