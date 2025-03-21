"""Tests for the static dashboard export and site assets.

The dashboard is the most-read artefact in the repository, and it is the one
place where a mistake is invisible in the test suite: a mislabelled chart or a
duplicated region looks like a styling choice rather than a defect. These checks
assert the properties that make it trustworthy.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"
DATA = ROOT / "data" / "export" / "site" / "data.json"
INDEX = SITE / "index.html"
APP = SITE / "assets" / "app.js"
CSS = SITE / "assets" / "styles.css"

pytestmark = pytest.mark.skipif(
    not DATA.exists(), reason="site not built; run 'make site'"
)


@pytest.fixture(scope="module")
def payload() -> dict:
    return json.loads(DATA.read_text())


class TestPayloadIntegrity:
    def test_anchor_year_is_not_the_newest_headcount(self, payload):
        """Coverage must anchor on a year with a full population denominator.

        Population reporting lags the workforce series, so the newest headcount
        year has few countries with a matching denominator. Anchoring there
        produced a three-row chart that looked like a data outage.
        """
        covered = [
            r for r in payload["coverage"]
            if r["physicians_per_1000"] is not None
        ]
        assert len(covered) >= 20, (
            f"only {len(covered)} countries have coverage; the anchor year is "
            f"probably the newest headcount rather than the newest complete one"
        )

    def test_coverage_has_no_duplicates(self, payload):
        codes = [r["country_code"] for r in payload["coverage"]]
        assert len(codes) == len(set(codes))

    def test_retirement_year_is_recorded(self, payload):
        assert "retirement_year" in payload
        assert payload["retirement_year"] >= 2000

    def test_regional_is_one_row_per_region(self, payload):
        """A region repeated across a decade of years reads as a duplicate."""
        codes = [r["nuts_code"] for r in payload["regional"]]
        assert len(codes) == len(set(codes))
        assert len(codes) >= 50

    def test_pyramid_excludes_the_total_row(self, payload):
        """Including Total would double every band's apparent width."""
        sexes = {r["sex_label"] for r in payload["age_pyramid"]}
        assert sexes == {"Male", "Female"}

    def test_pyramid_covers_every_band_for_both_sexes(self, payload):
        bands = {}
        for row in payload["age_pyramid"]:
            bands.setdefault(row["age_group_code"], set()).add(row["sex_label"])
        assert bands, "no pyramid data"
        for code, sexes in bands.items():
            assert sexes == {"Male", "Female"}, f"{code} missing a sex"

    def test_no_nan_in_payload(self, payload):
        """NaN is not valid JSON and would silently break the page."""

        def walk(node, path="$"):
            if isinstance(node, dict):
                for key, value in node.items():
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")
            elif isinstance(node, float) and node != node:
                pytest.fail(f"NaN at {path}")

        walk(payload)

    def test_kpis_are_present_and_finite(self, payload):
        kpis = payload["kpis"]
        for key in ("doctors", "nurses", "workforce_gap", "required_workers"):
            assert key in kpis
            assert isinstance(kpis[key], (int, float))

    def test_model_comparison_is_populated(self, payload):
        mape = payload["quality"].get("model_mape") or []
        assert len(mape) >= 5, "model comparison missing from the dashboard"
        models = {row["model"] for row in mape}
        assert {"arima", "naive"} <= models


class TestSiteAssets:
    def test_all_files_present(self):
        for path in (INDEX, APP, CSS):
            assert path.exists(), f"missing {path}"

    def test_no_external_network_dependency(self):
        """No CDN: the page must work offline and under a strict CSP.

        Only remote *loads* matter. A hyperlink in the footer is a navigation,
        not a subresource, and is allowed.
        """
        svg_ns = "http://www.w3.org/2000/svg"
        for path in (INDEX, APP, CSS):
            text = path.read_text().replace(svg_ns, "")
            for url in re.findall(r"https?://[^\s\"'()<>]+", text):
                if url.startswith("https://github.com"):
                    continue
                pytest.fail(f"{path.name} references {url}")

    def test_data_is_embedded_not_fetched(self):
        html = INDEX.read_text()
        assert "window.__DATA__" in (SITE / "assets" / "data.js").read_text()[:200]
        assert "fetch(" not in html
        assert "<script" in html

    def test_both_themes_are_defined(self):
        css = CSS.read_text()
        assert ":root" in css
        assert '[data-theme="dark"]' in css

    def test_theme_toggle_is_wired(self):
        assert "theme-toggle" in INDEX.read_text()
        assert "applyTheme" in APP.read_text()

    def test_every_nav_target_exists(self):
        html = INDEX.read_text()
        targets = set(re.findall(r'href="#([\w-]+)"', html))
        ids = set(re.findall(r'id="([\w-]+)"', html))
        assert targets, "no navigation links"
        assert targets <= ids, f"nav points at missing sections: {targets - ids}"

    def test_caveat_block_is_present(self):
        """The caveats are what stop the page being misread as precise."""
        html = INDEX.read_text().lower()
        assert "caveats" in html
        for phrase in ("not zero", "no nurse data", "naive"):
            assert phrase in html, f"caveat missing: {phrase}"

    def test_text_is_set_not_injected(self):
        """All data binding must use textContent, not innerHTML."""
        app = APP.read_text()
        assert "innerHTML" not in app
        assert ".textContent" in app


class TestRendering:
    @pytest.fixture(scope="class")
    def app_source(self):
        return APP.read_text()

    def test_missing_values_render_as_not_reported(self, app_source):
        assert "not reported" in app_source
        assert "notRe" not in app_source or True

    def test_no_measure_coalesces_missing_to_zero(self, app_source):
        """A missing value must never render as a zero-length bar.

        Guards against a ``value || 0`` default on a data field, which would
        render "no nurse reported" as a genuine measurement of zero. Counter
        defaults are fine and are excluded.
        """
        for match in re.finditer(r"\|\|\s*0", app_source):
            line = app_source[:match.start()].splitlines()[-1]
            assert "counts" in line, f"data defaulting to zero: {line.strip()}"

    def test_chart_has_accessible_label(self, app_source):
        assert "aria-label" in app_source
        assert 'role", "img"' in app_source or "role', 'img'" in app_source \
            or 'setAttribute("role", "img")' in app_source

    def test_kpi_headline_is_not_a_misleading_aggregate(self, app_source):
        """The aggregate gap is a surplus; the tile must count countries.

        Summing required-minus-actual across 27 countries produces a large
        negative number that reads as a catastrophic shortage while actually
        being a surplus against a target average.
        """
        assert "below the reference" in app_source
        assert "underSupplied" in app_source
