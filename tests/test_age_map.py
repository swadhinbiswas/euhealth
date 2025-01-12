"""Tests for age band reconciliation.

The invariant that matters: after mapping to canonical bands, the five bands
must still sum to the reported TOTAL for every country and year. If this
breaks, the workforce fact table is double-counting or dropping workers.
"""
from __future__ import annotations

import pandas as pd
import pytest

from ingestion import eurostat
from transform.age_map import (
    AGE_CODE_MAP,
    BAND_SOURCE_COUNT,
    summarise,
    to_canonical,
)


class TestMapping:
    def test_physician_ladder(self):
        assert AGE_CODE_MAP["phys"]["Y_LT35"] == "Y_LT35"
        assert AGE_CODE_MAP["phys"]["Y65-74"] == "Y_GE65"
        assert AGE_CODE_MAP["phys"]["Y_GE75"] == "Y_GE65"

    def test_nurse_under35_collapses_two_codes(self):
        # Nurses split under-35 into two codes; physicians do not.
        assert AGE_CODE_MAP["nurse"]["Y_LT25"] == "Y_LT35"
        assert AGE_CODE_MAP["nurse"]["Y25-34"] == "Y_LT35"

    def test_both_professions_align_on_shared_bands(self):
        for code in ("Y35-44", "Y45-54", "Y55-64"):
            assert AGE_CODE_MAP["phys"][code] == AGE_CODE_MAP["nurse"][code]
        # 65+ is assembled from two source codes in both professions.
        for code in ("Y65-74", "Y_GE75"):
            assert AGE_CODE_MAP["phys"][code] == "Y_GE65"
            assert AGE_CODE_MAP["nurse"][code] == "Y_GE65"

    def test_total_is_not_mapped(self):
        # TOTAL is an aggregate; mapping it would double count.
        assert to_canonical("phys", "TOTAL") is None
        assert to_canonical("nurse", "TOTAL") is None

    def test_unknown_code_returns_none(self):
        assert to_canonical("phys", "NOT_A_CODE") is None

    def test_unknown_profession_returns_none(self):
        assert to_canonical("dentist", "Y35-44") is None

    def test_source_counts_show_which_bands_need_summing(self):
        # Y_GE65 and nurse Y_LT35 are each fed by two source codes.
        assert BAND_SOURCE_COUNT["Y_GE65"]["phys"] == 2
        assert BAND_SOURCE_COUNT["Y_LT35"]["nurse"] == 2
        assert BAND_SOURCE_COUNT["Y35_44"]["phys"] == 1

    def test_summarise_is_documented(self):
        text = summarise()
        assert "Y65-74->Y_GE65" in text
        assert "Y25-34->Y_LT35" in text


def _reconcile(frame: pd.DataFrame, profession: str) -> pd.DataFrame:
    """Sum canonical bands per country/year and compare to TOTAL.

    Completeness is judged by the number of *source* age codes present, not the
    number of canonical bands. Counting canonical bands hides a gap: a row can
    appear to show all 5 bands while Y_GE75 is simply absent, so Y_GE65
    silently covers only Y65-74.
    """
    expected = 1 + len(AGE_CODE_MAP[profession])  # TOTAL + each source code
    totals = (
        frame[frame["age"] == "TOTAL"]
        .groupby(["geo", "time"])["value"]
        .sum()
        .rename("reported")
    )
    source_codes = (
        frame.groupby(["geo", "time"])["age"].nunique().rename("source_codes")
    )
    frame = frame.copy()
    frame["band"] = frame["age"].map(lambda c: to_canonical(profession, c))
    bands = frame[frame["band"].notna()]
    mapped = bands.groupby(["geo", "time"])["value"].sum().rename("mapped")
    out = pd.concat([mapped, totals, source_codes], axis=1).dropna()
    out["complete"] = out["source_codes"] == expected
    out["pct_diff"] = (out["mapped"] - out["reported"]) / out["reported"] * 100
    return out


_CACHE: dict[str, pd.DataFrame] = {}


def _load(loader):
    """Load once per session; payloads are cached on disk so this is cheap."""
    key = loader.__name__
    if key not in _CACHE:
        _CACHE[key] = loader()
    return _CACHE[key]


@pytest.mark.parametrize("loader,profession", [
    (eurostat.load_physicians, "phys"),
    (eurostat.load_nurses, "nurse"),
])
class TestReconciliationAgainstEurostat:
    """Validated against real cached Eurostat payloads."""

    @pytest.fixture
    def frame(self, loader):
        df = _load(loader)
        if df.empty:
            pytest.skip("no cached payload")
        return df[df["sex"] == "T"]

    def test_bands_sum_to_reported_total(self, frame, profession):
        """On fully reported country-years the bands must reconcile.

        Tolerance is 1%: Eurostat's own TOTAL is not always exactly the sum of
        its age breakdown (observed residual <= 0.5% on complete rows), so an
        exact-equality assertion would fail on the source, not on our logic.
        """
        check = _reconcile(frame, profession)
        complete = check[check["complete"]]
        assert len(complete) > 100, "expected a substantial complete sample"
        offenders = complete[complete["pct_diff"].abs() > 1.0]
        assert offenders.empty, (
            f"canonical bands do not reconcile to TOTAL for "
            f"{len(offenders)} fully-reported country-years, e.g.\n{offenders.head()}"
        )

    def test_incomplete_reporting_is_detectable(self, frame, profession):
        """Countries reporting TOTAL only must be flagged, not silently kept."""
        check = _reconcile(frame, profession)
        incomplete = check[~check["complete"]]
        expected = 1 + len(AGE_CODE_MAP[profession])
        assert (incomplete["source_codes"] < expected).all()
        assert len(incomplete) > 0, "expected some incomplete reporting to detect"

    def test_every_source_code_is_mapped(self, frame, profession):
        codes = set(frame["age"].unique()) - {"TOTAL"}
        unmapped = {c for c in codes if to_canonical(profession, c) is None}
        assert not unmapped, f"unmapped age codes: {unmapped}"

    def test_total_is_not_double_counted(self, frame, profession):
        mapped = frame["age"].map(lambda c: to_canonical(profession, c))
        assert not (mapped == "Y_LT35").any() or frame["age"].eq("TOTAL").sum() > 0
        # TOTAL rows must carry no canonical band.
        assert mapped[frame["age"] == "TOTAL"].isna().all()