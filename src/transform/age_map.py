"""Age band reconciliation across professions.

Eurostat publishes **different age ladders** for different professions:

* physicians (``hlth_rs_phys``):  Y_LT35, Y35-44, Y45-54, Y55-64, Y65-74, Y_GE75
* nurses (``hlth_rs_nurse``):Y_LT25, Y25-34, Y35-44, Y45-54, Y55-64, Y65-74, Y_GE75

Neither ladder contains ``Y_GE65``, which the original ``config.AGE_BANDS``
assumed. Raw codes must never be joined directly across professions: nurses
would lose every under-35 worker and the 65+ band would double-count.

This module produces an explicit mapping from each source code to a canonical
band, so the reconciliation is a documented table rather than an ad-hoc rename.
"""
from __future__ import annotations

from ingestion.registry import CANONICAL_AGE_BANDS

#: profession key -> {eurostat_age_code: canonical_band}
AGE_CODE_MAP: dict[str, dict[str, str]] = {
    profession: {
        code: band
        for band, spec in CANONICAL_AGE_BANDS.items()
        for code in spec[profession]
    }
    for profession in ("phys", "nurse")
}

#: Reverse lookup: canonical band -> number of source codes that roll into it.
#: A band fed by more than one code needs the values summed, not taken.
BAND_SOURCE_COUNT: dict[str, dict[str, int]] = {
    band: {
        profession: len(spec[profession])
        for profession in ("phys", "nurse")
    }
    for band, spec in CANONICAL_AGE_BANDS.items()
}


def to_canonical(profession: str, age_code: str) -> str | None:
    """Map one raw Eurostat age code to a canonical band.

    ``TOTAL`` is an aggregate over all ages and is deliberately not mapped: it
    belongs on its own measure, never inside an age-partitioned sum, or the
    total would be counted twice.
    """
    if age_code == "TOTAL":
        return None
    return AGE_CODE_MAP.get(profession, {}).get(age_code)


def summarise() -> str:
    """Human-readable description of the mapping, for the data dictionary."""
    lines = []
    for profession in ("phys", "nurse"):
        mapping = AGE_CODE_MAP[profession]
        pairs = ", ".join(f"{code}->{band}" for code, band in mapping.items())
        lines.append(f"{profession:6s} {pairs}")
    return "\n".join(lines)


if __name__ == "__main__":
    print("Eurostat age code -> canonical band")
    print(summarise())