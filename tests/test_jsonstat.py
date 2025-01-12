"""Tests for JSON-stat decoding, against a real captured Eurostat payload."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ingestion.jsonstat import (
    STATUS_FLAGS,
    category_codes,
    flatten_jsonstat,
    is_missing,
    unravel,
)

FIXTURE = Path(__file__).parent / "fixtures" / "hlth_rs_phys_DE.json"


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(FIXTURE.read_text())


class TestUnravel:
    def test_flat_index_decodes_to_positions(self):
        # Verified against live DE payload: key 60 = age Y_GE75, sex F, time 2023.
        assert unravel(60, [1, 1, 7, 3, 1, 3]) == [0, 0, 6, 2, 0, 0]

    def test_first_and_last_cells(self):
        size = [1, 1, 7, 3, 1, 3]
        assert unravel(0, size) == [0, 0, 0, 0, 0, 0]
        assert unravel(62, size) == [0, 0, 6, 2, 0, 2]

    def test_last_dimension_varies_fastest(self):
        assert unravel(1, [1, 1, 7, 3, 1, 3]) == [0, 0, 0, 0, 0, 1]

    def test_rejects_out_of_range(self):
        with pytest.raises(ValueError):
            unravel(63, [1, 1, 7, 3, 1, 3])

    def test_rejects_negative(self):
        with pytest.raises(ValueError):
            unravel(-1, [1, 1, 2])

    def test_row_major_is_a_bijection(self):
        size = [2, 3, 4]
        seen = {tuple(unravel(i, size)) for i in range(24)}
        assert len(seen) == 24


class TestCategoryCodes:
    def test_orders_by_position_not_alphabetical(self):
        dim = {"age": {"category": {"index": {"TOTAL": 0, "Y_LT35": 1, "Y35-44": 2}}}}
        assert category_codes(dim, "age") == ["TOTAL", "Y_LT35", "Y35-44"]

    def test_handles_unordered_input(self):
        dim = {"t": {"category": {"index": {"c": 2, "a": 0, "b": 1}}}}
        assert category_codes(dim, "t") == ["a", "b", "c"]

    def test_handles_list_form(self):
        assert category_codes({"t": {"category": {"index": ["x", "y"]}}}, "t") == ["x", "y"]

    def test_missing_key_is_empty(self):
        assert category_codes({}, "nope") == []


class TestIsMissing:
    @pytest.mark.parametrize("token", ["", ":", "..", "...", "NaN", "nan", "null"])
    def test_missing_tokens(self, token):
        assert is_missing(token)

    def test_zero_is_not_missing(self):
        # The critical distinction: a reported 0 is an observation.
        assert not is_missing(0)
        assert not is_missing("0")

    def test_nan_float(self):
        assert is_missing(float("nan"))

    def test_number_is_present(self):
        assert not is_missing(42)


class TestFlatten:
    def test_row_count_matches_payload(self, payload):
        assert len(flatten_jsonstat(payload)) == len(payload["value"])

    def test_known_value_decodes_correctly(self, payload):
        rows = flatten_jsonstat(payload)
        match = [
            r for r in rows
            if r["age"] == "TOTAL" and r["sex"] == "T" and r["time"] == "2023"
        ]
        assert len(match) == 1
        assert match[0]["value"] == 388343
        assert match[0]["geo"] == "DE"

    def test_sparse_high_index_decodes_correctly(self, payload):
        """Guards the row-major arithmetic against an off-by-one.

        Key 60 is the last observed cell: age Y_GE75, sex F, time 2023,
        value 848, flagged estimated. This is the case a naive
        `key.split(',')` implementation silently drops.
        """
        rows = flatten_jsonstat(payload)
        match = [
            r for r in rows
            if r["age"] == "Y_GE75" and r["sex"] == "F" and r["time"] == "2023"
        ]
        assert len(match) == 1
        assert match[0]["value"] == 848
        assert match[0]["status"] == "e"

    def test_every_row_has_all_dimensions(self, payload):
        for row in flatten_jsonstat(payload):
            assert set(row) >= set(payload["id"])
            assert all(row[d] is not None for d in payload["id"])

    def test_status_flags_preserved(self, payload):
        rows = flatten_jsonstat(payload)
        flagged = [r for r in rows if "status" in r]
        assert flagged, "fixture contains estimated values"
        for row in flagged:
            assert row["status_label"] == STATUS_FLAGS[row["status"]]

    def test_status_can_be_suppressed(self, payload):
        assert all("status" not in r for r in flatten_jsonstat(payload, include_status=False))

    def test_missing_observations_are_dropped_not_zeroed(self):
        doc = {
            "id": ["geo", "time"],
            "size": [1, 2],
            "dimension": {
                "geo": {"category": {"index": {"DE": 0}}},
                "time": {"category": {"index": {"2023": 0, "2024": 1}}},
            },
            "value": {"0": 100, "1": ":"},
        }
        rows = flatten_jsonstat(doc)
        assert len(rows) == 1
        assert rows[0]["value"] == 100

    def test_size_mismatch_returns_empty(self):
        doc = {"id": ["geo", "time"], "size": [1], "dimension": {}, "value": {"0": 1}}
        assert flatten_jsonstat(doc) == []

    def test_non_dict_payload_is_safe(self):
        assert flatten_jsonstat(None) == []
        assert flatten_jsonstat([]) == []

    def test_single_dimension_id_as_string(self):
        doc = {
            "id": "geo",
            "size": [1],
            "dimension": {"geo": {"category": {"index": {"DE": 0}}}},
            "value": {"0": 7},
        }
        assert flatten_jsonstat(doc) == [{"geo": "DE", "value": 7}]

    def test_dense_list_value_form(self):
        doc = {
            "id": ["geo", "time"],
            "size": [1, 2],
            "dimension": {
                "geo": {"category": {"index": {"DE": 0}}},
                "time": {"category": {"index": {"2023": 0, "2024": 1}}},
            },
            "value": [5, 6],
        }
        rows = flatten_jsonstat(doc)
        assert [r["value"] for r in rows] == [5, 6]