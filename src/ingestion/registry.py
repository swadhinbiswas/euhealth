"""Verified Eurostat dataset registry.

Every entry here was confirmed against the live dissemination API and the
official Eurostat table-of-contents catalogue. Dimensions are recorded
explicitly because a wrong dimension name returns
``400 INVALID_QUERY_DIMENSION`` rather than an empty result, and that
distinction is easy to lose.

Two API constraints shape every loader in this module:

1. **Only one country per request.** A multi-valued ``geo=`` collapses to
   ``size 0`` and returns ``200 OK`` with no values.
2. **Only one value per dimension.** ``age=TOTAL,Y35-44`` behaves the same
   way. Loaders therefore pull the full cube for one country and filter
   locally, which also preserves the observation ``status`` flags.
"""
from __future__ import annotations

from dataclasses import dataclass, field

EU27 = [
    "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE", "EL", "HU",
    "IE", "IT", "LV", "LT", "LU", "MT", "NL", "AT", "PL", "PT", "RO", "SK",
    "SI", "ES", "SE",
]

# Countries with reliable national reporting for the workforce datasets.
# A missing country is reported as a gap, never silently imputed.
EU27_ISO3 = {
    "BE": "BEL", "BG": "BGR", "HR": "HRV", "CY": "CYP", "CZ": "CZE",
    "DK": "DNK", "EE": "EST", "FI": "FIN", "FR": "FRA", "DE": "DEU",
    "EL": "GRC", "GR": "GRC", "HU": "HUN", "IE": "IRL", "IT": "ITA",
    "LV": "LVA", "LT": "LTU", "LU": "LUX", "MT": "MLT", "NL": "NLD",
    "AT": "AUT", "PL": "POL", "PT": "PRT", "RO": "ROU", "SK": "SVK",
    "SI": "SVN", "ES": "ESP", "SE": "SWE",
}


@dataclass(frozen=True)
class Dataset:
    """One Eurostat table plus the dimension codes needed to use it."""

    code: str
    label: str
    dims: tuple[str, ...]
    #: Categories that are real observations. ``TOTAL`` is an aggregate and is
    #: excluded from age-partitioned facts to prevent double counting.
    measure_dims: tuple[str, ...] = field(default=())
    since: int | None = None
    unit: str = "NR"
    notes: str = ""

    @property
    def cache_prefix(self) -> str:
        return self.code


# --- Workforce: the analytical core -----------------------------------------
# NOTE: physicians and nurses have genuinely different age ladders.
#   hlth_rs_phys : Y_LT35,   Y35-44, Y45-54, Y55-64, Y65-74, Y_GE75
#   hlth_rs_nurse: Y_LT25, Y25-34, Y35-44, Y45-54, Y55-64, Y65-74, Y_GE75
# There is no ``Y_GE65`` code anywhere; the configured AGE_BANDS value of
# ``Y_GE65`` was wrong and is reconciled by src/transform/age_map.py.
PHYS_AGES = ("TOTAL", "Y_LT35", "Y35-44", "Y45-54", "Y55-64", "Y65-74", "Y_GE75")
NURSE_AGES = (
    "TOTAL", "Y_LT25", "Y25-34", "Y35-44", "Y45-54", "Y55-64", "Y65-74", "Y_GE75",
)

DATASETS: dict[str, Dataset] = {
    "physicians": Dataset(
        code="hlth_rs_phys",
        label="Physicians by sex and age",
        dims=("freq", "unit", "age", "sex", "geo", "time"),
        measure_dims=("age", "sex"),
        since=2000,
        notes="National only. No NUTS dimension exists.",
    ),
    "nurses": Dataset(
        code="hlth_rs_nurse",
        label="Nurses and midwives by sex and age",
        dims=("freq", "unit", "age", "sex", "geo", "time"),
        measure_dims=("age", "sex"),
        since=2000,
    ),
    "staff_isco": Dataset(
        code="hlth_rs_prs1",
        label="Health staff by ISCO and work status",
        dims=("freq", "unit", "wstatus", "isco08", "geo", "time"),
        measure_dims=("wstatus", "isco08"),
        since=2000,
    ),
    "staff_specialty": Dataset(
        code="hlth_rs_prs2",
        label="Health staff by medical speciality and work status",
        dims=("freq", "unit", "wstatus", "med_spec", "geo", "time"),
        measure_dims=("wstatus", "med_spec"),
        since=2000,
        notes="med_spec: PHYS MWS NRS DENT PHARM PER_CARE PHYSIO",
    ),
    "consultants": Dataset(
        code="hlth_rs_spec",
        label="Physicians by consultant/specialist status",
        dims=("freq", "unit", "med_spec", "geo", "time"),
        measure_dims=("med_spec",),
        since=2000,
    ),
    "graduates": Dataset(
        code="hlth_rs_grd",
        label="Graduating health professionals",
        dims=("freq", "unit", "isco08", "geo", "time"),
        measure_dims=("isco08",),
        since=2000,
        notes="Training pipeline: future domestic supply.",
    ),
    "beds": Dataset(
        code="hlth_rs_bds",
        label="Hospital beds by facility type",
        dims=("freq", "unit", "facility", "geo", "time"),
        measure_dims=("facility",),
        since=2000,
    ),
    "beds_ownership": Dataset(
        code="hlth_rs_bds2",
        label="Hospital beds by ownership",
        dims=("freq", "unit", "owner", "geo", "time"),
        measure_dims=("owner",),
        since=2000,
    ),
    "icu_beds": Dataset(
        code="hlth_rs_bdsicu",
        label="Intensive care beds",
        dims=("freq", "unit", "facility", "statinfo", "geo", "time"),
        measure_dims=("facility", "statinfo"),
        since=2000,
        notes="Drives the critical-care component of hospital risk.",
    ),
    "tech_staff": Dataset(
        code="hlth_rs_tech",
        label="Health technicians by facility",
        dims=("freq", "unit", "facility", "geo", "time"),
        measure_dims=("facility",),
        since=2000,
    ),
    "worker_migration": Dataset(
        code="hlth_rs_wkmg",
        label="Health workers by place of training",
        dims=("freq", "unit", "isco08", "tngplace", "geo", "time"),
        measure_dims=("isco08", "tngplace"),
        since=2000,
        notes="tngplace FOR/NAT_FOR = foreign-trained dependency.",
    ),
    # --- Demand and population ---------------------------------------------
    "hospital_days": Dataset(
        code="hlth_co_hosday",
        label="Hospital days by ICD-10 group",
        dims=("freq", "age", "indic_he", "unit", "sex", "icd10", "geo", "time"),
        measure_dims=("age", "sex", "icd10"),
        since=2015,
        notes="Very large table. Patient demand driver.",
    ),
    "population_nuts": Dataset(
        code="demo_r_pjangrp3",
        label="Population by age and sex at NUTS level",
        dims=("freq", "sex", "unit", "age", "geo", "time"),
        measure_dims=("age", "sex"),
        unit="NR",
        notes="Queried with geoLevel=, not geo=. Single time point per pull.",
    ),
    "population_country": Dataset(
        code="demo_pjanind",
        label="National population indicators",
        dims=("freq", "indic_de", "geo", "time"),
        measure_dims=("indic_de",),
        since=2000,
        notes="No age/sex dims; indic_de carries PC_Y0_14, MEDAGEPOP etc.",
    ),
    "life_expectancy": Dataset(
        code="demo_mlexpec",
        label="Life expectancy at birth",
        dims=("freq", "unit", "sex", "age", "geo", "time"),
        measure_dims=("sex", "age"),
        since=2000,
        unit="YR",
    ),
    "mortality": Dataset(
        code="demo_magec",
        label="Deaths by age and sex",
        dims=("freq", "unit", "sex", "age", "geo", "time"),
        measure_dims=("sex", "age"),
        since=2000,
    ),
    "density": Dataset(
        code="demo_r_d3dens",
        label="Population density",
        dims=("freq", "unit", "geo", "time"),
        measure_dims=(),
        since=2000,
        unit="PER_KM2",
    ),
    "projections": Dataset(
        code="proj_25np",
        label="EU population projections, 2025 baseline",
        dims=("freq", "sex", "age", "unit", "projection", "geo", "time"),
        measure_dims=("sex", "age", "projection"),
        unit="PER",
        notes="2025 baseline supersedes proj_23np. Scenarios: BSL LFRT LMRT HMIGR LMIGR NMIGR.",
    ),
    # --- Labour market ------------------------------------------------------
    "labour_status": Dataset(
        code="lfsa_egaps",
        label="Labour force by work status",
        dims=("freq", "unit", "sex", "age", "wstatus", "geo", "time"),
        measure_dims=("sex", "age", "wstatus"),
        since=2000,
        unit="THS_PER",
        notes="EURES substitute: recruitment pressure proxy.",
    ),
    "labour_education": Dataset(
        code="lfsa_egaed",
        label="Labour force by education level",
        dims=("freq", "unit", "sex", "age", "isced11", "geo", "time"),
        measure_dims=("sex", "age", "isced11"),
        since=2000,
        unit="THS_PER",
    ),
}

#: Dataset codes that were referenced in the original codebase but do not
#: exist in the Eurostat catalogue. Kept so the migration is auditable and so
#: no one reintroduces them.
NON_EXISTENT_CODES = (
    "hlth_rs_wdsy",   # nurses by sex/age -> hlth_rs_nurse
    "hlth_rs_empt",   # vacancies -> no Eurostat equivalent; EURES has no API
    "lfsa_3une_r",    # -> lfsa_egaps / lfsa_egaed
    "hlth_ges11_hf",  # health expenditure -> not published under this code
    "hlth_inpatient", # -> hlth_co_hosday
    "hlth_care",      # -> hlth_co_hosday
    "censis_r",       # -> not in catalogue
)

#: Canonical age bands for the warehouse, and the Eurostat codes that feed
#: each one. Physicians and nurses are aggregated up into the same bands.
CANONICAL_AGE_BANDS: dict[str, dict] = {
    "Y_LT35": {
        "label": "Under 35", "min_age": 18, "max_age": 34,
        "phys": ("Y_LT35",),
        "nurse": ("Y_LT25", "Y25-34"),
    },
    "Y35_44": {
        "label": "35-44", "min_age": 35, "max_age": 44,
        "phys": ("Y35-44",), "nurse": ("Y35-44",),
    },
    "Y45_54": {
        "label": "45-54", "min_age": 45, "max_age": 54,
        "phys": ("Y45-54",), "nurse": ("Y45-54",),
    },
    "Y55_64": {
        "label": "55-64", "min_age": 55, "max_age": 64,
        "phys": ("Y55-64",), "nurse": ("Y55-64",),
    },
    "Y_GE65": {
        "label": "65+", "min_age": 65, "max_age": 120,
        "phys": ("Y65-74", "Y_GE75"),
        "nurse": ("Y65-74", "Y_GE75"),
    },
}

SEX = {"T": "Total", "M": "Male", "F": "Female"}