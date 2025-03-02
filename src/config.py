"""Central configuration for the EU Healthcare Workforce Analytics Platform."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA = ROOT / "data"
RAW = DATA / "raw"                 # immutable landing zone (source fidelity)
BRONZE = DATA / "bronze"           # typed, minimal cleaning
SILVER = DATA / "silver"           # conformed, business keys, deduped
GOLD = DATA / "gold"               # star schema, analysis ready
GEO = DATA / "geo"
MODELS = DATA / "models"
EXPORT = DATA / "export"           # Power BI / CSV exports
LOGS = DATA / "logs"

for _d in (RAW, BRONZE, SILVER, GOLD, GEO, MODELS, EXPORT, LOGS):
    _d.mkdir(parents=True, exist_ok=True)

WAREHOUSE = DATA / "healthcare_dw.duckdb"

# --- Eurostat dissemination API -------------------------------------------
EUROSTAT_BASE = (
    "https://ec.europa.eu/eurostat/api/dissemination/statistics/1.0/data/"
)

# --- Official NUTS 2021 boundaries (Eurostat GISCO) -----------------------
GISCO_NUTS_BASE = (
    "https://gisco-services.ec.europa.eu/distribution/v2/nuts/geojson/"
)
NUTS_LEVELS = {
    "level0": "NUTS_RG_10M_2021_3035_LEVL_0.geojson",
    "level1": "NUTS_RG_10M_2021_3035_LEVL_1.geojson",
    "level2": "NUTS_RG_10M_2021_3035_LEVL_2.geojson",
    "level3": "NUTS_RG_10M_2021_3035_LEVL_3.geojson",
}

# --- OECD ----------------------------------------------------------------
OECD_BASE = "https://sdmx.oecd.org/public/rest/data/"

# --- EU member states -----------------------------------------------------
EU27 = [
    "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "AT", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
]
EU27_NAMES = {
    "BE": "Belgium", "BG": "Bulgaria", "HR": "Croatia", "CY": "Cyprus",
    "CZ": "Czechia", "DK": "Denmark", "EE": "Estonia", "FI": "Finland",
    "FR": "France", "DE": "Germany", "EL": "Greece", "HU": "Hungary",
    "IE": "Ireland", "IT": "Italy", "LV": "Latvia", "LT": "Lithuania",
    "LU": "Luxembourg", "MT": "Malta", "NL": "Netherlands", "AT": "Austria",
    "PL": "Poland", "PT": "Portugal", "RO": "Romania", "SK": "Slovakia",
    "SI": "Slovenia", "ES": "Spain", "SE": "Sweden",
}
ISO2_TO_ISO3 = {
    "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP", "CZ": "CZE",
    "DK": "DNK", "EE": "EST", "FI": "FIN", "FR": "FRA", "DE": "DEU",
    "EL": "GRC", "GR": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA",
    "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT", "NL": "NLD",
    "AT": "AUT", "PL": "POL", "PT": "PRT", "RO": "ROU", "SK": "SVK",
    "SI": "SVN", "ES": "ESP", "SE": "SWE",
    "UK": "GBR", "NO": "NOR", "CH": "CHE", "IS": "ISL",
}

# ISO3 -> country that has full national reporting
EU27_ISO3 = [ISO2_TO_ISO3[c] for c in EU27]

# Verified NUTS 2 / NUTS 3 identifiers are pulled at runtime from the Eurostat
# population table rather than hard-coded; these prefixes drive that mapping.
NUTS_PREFIX = {c: ISO2_TO_ISO3[c] for c in EU27}

PROFESSIONS = [
    "Physician",
    "Nurse",
    "Dentist",
    "Pharmacist",
    "Midwife",
    "All health professionals",
]

PROFESSION_KEY = {
    "physician": "Physician",
    "nurse": "Nurse",
    "dentist": "Dentist",
    "pharmacist": "Pharmacist",
    "midwife": "Midwife",
    "all health professionals": "All health professionals",
}

# Eurostat hlth_rs_* codes -> internal profession
DATASET_PROFESSION = {
    "hlth_rs_phys": "Physician",
    "hlth_rs_nurse": "Nurse",
}

# Canonical warehouse age bands.
#
# NOTE: these are *canonical* bands, not Eurostat codes. The raw tables do not
# contain "Y_GE65" or "Y35_44" -- they use Y65-74/Y_GE75 and Y35-44. Physicians
# and nurses also have different ladders (physicians start at Y_LT35, nurses at
# Y_LT25 + Y25-34). The reconciliation mapping lives in
# ingestion.registry.CANONICAL_AGE_BANDS; never join on raw age codes directly.
AGE_BANDS = {
    "Y_LT35": ("Under 35", 18, 34),
    "Y35_44": ("35-44", 35, 44),
    "Y45_54": ("45-54", 45, 54),
    "Y55_64": ("55-64", 55, 64),
    "Y_GE65": ("65+", 65, 120),
}

SEX = {"T": "Total", "M": "Male", "F": "Female"}

SECTORS = [
    "Hospital",
    "Primary Care",
    "Long-Term Care",
    "Community Care",
    "Pharmacy",
    "Public Health",
]

# Reference ratios used for need estimation (per 1,000 population).
# Benchmarks from OECD / WHO European health workforce reports.
#
# These are per profession and must not be collapsed into one value. Applying
# the physician ratio to nurses measures them against a doctor-sized target
# and reports them as 250% staffed.
REFERENCE_DOCTORS_PER_1000 = 3.3
REFERENCE_NURSES_PER_1000 = 9.0
REFERENCE_POP_AGE_65_PLUS_SHARE = 0.20

#: Profession code -> reference workers per 1,000 population.
REFERENCE_BY_PROFESSION = {
    "PHYS": REFERENCE_DOCTORS_PER_1000,
    "NURS": REFERENCE_NURSES_PER_1000,
    # No published benchmark for these, so they are excluded from the
    # shortage fact rather than measured against a proxy that does not apply.
}

# Planning horizon
FORECAST_YEARS = [2025, 2027, 2030, 2035]

REQUEST_TIMEOUT = 120
REQUEST_RETRIES = 3
USER_AGENT = "eu-health-workforce-platform/1.0 (+analytics portfolio)"

RANDOM_SEED = 42


def env_flag(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).lower() in ("1", "true", "yes")