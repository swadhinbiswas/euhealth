"""Checks that the numbers this repository quotes about itself stay true.

The README and the dashboard footer both state a test count. Both were 313
while the footer said 239, because the figures were copied by hand into prose
and into a generated page that nobody regenerated. A claim a reviewer can
check should be checked, so the count is measured from pytest and compared
against what is shipped.

These tests deliberately live in their own module. ``test_dashboard`` skips
entirely when the gitignored site export is absent, which is the normal state
in CI, and a guard that silently skips in CI guards nothing.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def collected_count() -> int:
    """Number of tests pytest collects, ignoring any skip conditions."""
    out = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", "--no-header"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    match = re.search(r"^(\d+) tests? collected", out.stdout, re.MULTILINE)
    assert match, out.stdout[-500:]
    return int(match.group(1))


class TestQuotedTestCount:
    """Every stated test count must equal the number of tests collected."""

    def test_footer_matches_the_suite(self, collected_count):
        html = (ROOT / "site" / "index.html").read_text(encoding="utf-8")
        claim = re.search(r"quality gate on every build, ([\d,]+) tests", html)
        assert claim, "footer must state a test count"

        shipped = int(claim.group(1).replace(",", ""))
        assert shipped == collected_count, (
            f"footer claims {shipped} tests but pytest collects {collected_count}. "
            "Run 'make site' and commit the regenerated site/index.html."
        )

    def test_readme_matches_the_suite(self, collected_count):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        claims = [
            int(group.replace(",", ""))
            for match in re.finditer(
                r"# (\d[\d,]*) tests|^\s*tests/\s+(\d[\d,]*) tests",
                readme,
                re.M,
            )
            for group in match.groups()
            if group
        ]
        assert claims, "README no longer states a test count"

        stale = [c for c in claims if c != collected_count]
        assert not stale, (
            f"README claims {stale} but pytest collects {collected_count}"
        )
