"""JSON-stat decoding for the Eurostat dissemination API.

Eurostat returns **flat row-major integer keys** in ``value`` (``"0"``, ``"3"``,
``"60"``), not comma-separated index tuples. A position must therefore be
unravelled against the ``size`` vector with modular arithmetic.

Status flags in ``status`` are observation flags, not errors: ``e`` = estimated,
``be`` = break in series, ``p`` = provisional, ``c`` = confidential, ``b`` = the
value is nil. They are preserved on every row because an estimated figure is not
the same claim as an observed one, and the data quality layer needs the
distinction.
"""
from __future__ import annotations

from typing import Any

# Eurostat observation status codes -> human labels.
STATUS_FLAGS: dict[str, str] = {
    "b": "break_in_series",
    "be": "break_in_series_estimated",
    "c": "confidential",
    "d": "definition_unavailable",
    "e": "estimated",
    "f": "forecast",
    "n": "not_significant",
    "p": "provisional",
    "r": "revised",
    "s": "Eurostat_estimate",
    "u": "low_reliability",
    "z": "not_applicable",
    ":": "not_available",
}

# Tokens Eurostat uses for a missing observation. These must NOT become 0.
MISSING_TOKENS = ("", ":", "..", "...", "NaN", "nan", "null", "None")


def unravel(flat: int, size: list[int]) -> list[int]:
    """Convert a flat row-major index into per-dimension positions.

    >>> unravel(60, [1, 1, 7, 3, 1, 3])
    [0, 0, 6, 2, 0, 2]
    """
    if flat < 0:
        raise ValueError(f"negative flat index: {flat}")
    total = 1
    for s in size:
        total *= s
    if flat >= total:
        raise ValueError(f"flat index {flat} out of range for size {size}")
    out: list[int] = []
    for extent in reversed(size):
        out.append(flat % extent)
        flat //= extent
    return list(reversed(out))


def category_codes(dimension: dict, key: str) -> list[str]:
    """Return a dimension's codes ordered by their index position.

    Eurostat returns the category index as a mapping (usually in alphabetical
    order, with the integer position as the value). Position order is what the
    flat index refers to, so it must be restored before decoding.
    """
    cat = (dimension.get(key) or {}).get("category") or {}
    index = cat.get("index")
    if index is None:
        return []
    if isinstance(index, dict):
        try:
            return [code for code, _ in sorted(index.items(), key=lambda kv: kv[1])]
        except TypeError:
            return list(index)
    if isinstance(index, list):
        return list(index)
    return []


def is_missing(value: Any) -> bool:
    """True when Eurostat marks the observation as absent.

    A missing observation is a real gap in reporting and is dropped, never
    coerced to zero.
    """
    if value is None:
        return True
    if isinstance(value, str):
        return value.strip() in MISSING_TOKENS
    if isinstance(value, float):
        return value != value  # NaN
    return False


def flatten_jsonstat(
    payload: dict,
    include_status: bool = True,
) -> list[dict]:
    """Expand a Eurostat JSON-stat document into tidy row dicts.

    Each returned row maps dimension code -> category code, plus ``value`` and,
    when present, ``status``/``status_label``.
    """
    if not isinstance(payload, dict):
        return []

    dimension: dict[str, Any] = payload.get("dimension") or {}
    order: list[str] = payload.get("id") or list(dimension.keys())
    if isinstance(order, str):
        order = [order]

    size = list(payload.get("size") or [])
    if len(size) != len(order):
        # Cannot decode positions without a matching extent vector.
        return []

    categories = [category_codes(dimension, key) for key in order]
    values = payload.get("value") or {}
    status_map = payload.get("status") or {}
    if not isinstance(values, dict):
        # Some endpoints return a dense list instead of a sparse mapping.
        values = {str(i): v for i, v in enumerate(values or [])}

    rows: list[dict] = []
    for key, value in values.items():
        if is_missing(value):
            continue
        try:
            flat = int(key)
            positions = unravel(flat, size)
        except (ValueError, TypeError):
            continue

        row: dict[str, Any] = {}
        for name, codes, pos in zip(order, categories, positions):
            row[name] = codes[pos] if 0 <= pos < len(codes) else None

        try:
            row["value"] = float(value) if isinstance(value, str) else value
        except (TypeError, ValueError):
            continue

        if include_status:
            flag = status_map.get(key) or status_map.get(str(flat))
            if flag:
                row["status"] = flag
                row["status_label"] = STATUS_FLAGS.get(flag, flag)
        rows.append(row)

    return rows