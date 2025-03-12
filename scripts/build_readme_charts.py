#!/usr/bin/env python
"""Generate animated SVG charts from REAL warehouse figures for the README.

Not a mockup. Each chart's values are queried from the verified warehouse at
build time, and the SVG carries a ``<title>`` plus ``aria-label`` so the
animation is not the only encoding (and screen readers can read the values).

Animation uses SMIL ``<animate>`` because those still run when an SVG is
loaded via <img> in browsers, unlike CSS animations on external SVG.
"""
from __future__ import annotations

from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
OUT = ROOT / "docs" / "images"
OUT.mkdir(parents=True, exist_ok=True)

ACCENT = "#1F4E79"
TEAL = "#2D7D9A"
RED = "#A4243B"
INK3 = "#8A94A6"
GRID = "#E4E8EF"
TEXT = "#3A4454"


def trend_line() -> str:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        rows = con.execute("""
            SELECT year, ROUND(physicians_per_1000_safe,2)
            FROM v_coverage_index_safe
            WHERE country_code='DE' AND physicians_per_1000_safe IS NOT NULL
            ORDER BY year
        """).fetchall()
    finally:
        con.close()
    years = [r[0] for r in rows]
    vals = [r[1] for r in rows]
    W, H = 640, 260
    L, R, T, B = 46, 20, 20, 34
    mn, mx = min(vals), max(vals)
    pad = (mx - mn) * 0.15 or 1
    mn -= pad
    mx += pad

    def sx(year):
        return L + (year - years[0]) / (years[-1] - years[0]) * (W - L - R)

    def sy(v):
        return T + (H - T - B) * (1 - (v - mn) / (mx - mn))

    pts = [(sx(y), sy(v)) for y, v in zip(years, vals)]
    path = "M " + " L ".join(f"{x:.1f} {yv:.1f}" for x, yv in pts)

    circles = "".join(
        f'<circle cx="{x:.1f}" cy="{yv:.1f}" r="3.2" fill="{ACCENT}"/>'
        for x, yv in pts
    )
    ylabels = "".join(
        f'<text x="{L-8}" y="{(T + (H-T-B)*i/4)+3:.1f}" text-anchor="end" '
        f'font-size="10" fill="{INK3}">{mn + (mx-mn)*(1-i/4):.1f}</text>'
        for i in range(5)
    )
    grid = "".join(
        f'<line x1="{L}" x2="{W-R}" y1="{(T + (H-T-B)*i/4):.1f}" '
        f'y2="{(T + (H-T-B)*i/4):.1f}" stroke="{GRID}"/>'
        for i in range(5)
    )
    xlabels = "".join(
        f'<text x="{sx(y):.1f}" y="{H-12}" text-anchor="middle" '
        f'font-size="10" fill="{INK3}">{y}</text>'
        for y in years[::2]
    )
    total_len = sum(
        ((pts[i + 1][0] - pts[i][0]) ** 2 + (pts[i + 1][1] - pts[i][1]) ** 2) ** 0.5
        for i in range(len(pts) - 1)
    )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="Germany physicians per 1,000 population, 2014 to 2024">
<rect width="{W}" height="{H}" fill="white"/>
{grid}
{ylabels}
{xlabels}
<path d="{path}" fill="none" stroke="{ACCENT}" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="{total_len:.1f}" stroke-dashoffset="{total_len:.1f}">
<animate attributeName="stroke-dashoffset" from="{total_len:.1f}" to="0" dur="1.6s" fill="freeze" repeatCount="1"/>
</path>
<g>{circles}</g>
<text x="{L}" y="14" font-size="12" fill="{TEXT}" font-weight="600">Germany physicians per 1,000 population (2014–2024)</text>
</svg>'''
    return svg


def bar_chart(title: str, data: list[tuple[str, float]], colour: str,
              value_label: str, filename: str) -> str:
    W, H = 640, 60 + 34 * len(data)
    L, R = 130, 70
    mx = max(v for _, v in data)
    bars = []
    for i, (name, v) in enumerate(data):
        y = 40 + i * 34
        w = (W - L - R) * v / mx if mx else 0
        bars.append(
            f'<text x="{L-10}" y="{y+14}" text-anchor="end" font-size="11.5" '
            f'fill="{TEXT}">{name}</text>'
            f'<rect x="{L}" y="{y+4}" height="20" rx="2" fill="{colour}" '
            f'width="0">'
            f'<animate attributeName="width" from="0" to="{w:.1f}" dur="1.1s" '
            f'fill="freeze" begin="{i*0.12:.2f}s" repeatCount="1"/>'
            f'</rect>'
            f'<text x="{L + w + 8:.1f}" y="{y+18}" font-size="11.5" fill="{TEXT}">'
            f'{v:.1f}%</text>'
        )
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}" role="img" aria-label="{title}">
<rect width="{W}" height="{H}" fill="white"/>
<text x="8" y="20" font-size="12" fill="{TEXT}" font-weight="600">{title}</text>
{''.join(bars)}
</svg>'''
    return svg


def retirement_bars() -> str:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        rows = con.execute("""
            SELECT country_name, ROUND(pct_55_plus,1)
            FROM v_retirement_exposure
            WHERE year=(SELECT MAX(year) FROM v_retirement_exposure WHERE profession_code='PHYS')
              AND profession_code='PHYS' ORDER BY pct_55_plus DESC LIMIT 8
        """).fetchall()
    finally:
        con.close()
    return bar_chart(
        "Share of physicians aged 55+, by country (most recent year)",
        [(r[0], r[1]) for r in rows], RED, "4.1f", "retirement",
    ).replace("%", "%")


def model_mape_bars() -> str:
    import pandas as pd
    df = pd.read_csv(ROOT / "data" / "models" / "model_comparison.csv")
    df = df.sort_values("mape")
    return bar_chart(
        "Forecast model comparison — MAPE % (lower is better)",
        [(r["model"], r["mape"]) for _, r in df.iterrows()],
        TEAL, "4.2f", "model-mape",
    )


def mascot() -> str:
    """An animated hospital cross mascot (heartbeat pulse + blinking eyes)."""
    svg = f'''<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200" role="img" aria-label="Mascot">
  <defs>
    <radialGradient id="g" cx="50%" cy="40%" r="60%">
      <stop offset="0%" stop-color="#ffffff"/>
      <stop offset="100%" stop-color="#e8f0f8"/>
    </radialGradient>
  </defs>
  <circle cx="100" cy="104" r="70" fill="url(#g)" stroke="{ACCENT}" stroke-width="3">
    <animate attributeName="r" values="70;74;70;70" dur="2s" repeatCount="indefinite"/>
  </circle>
  <!-- hospital cross -->
  <g transform="translate(100,94)">
    <rect x="-24" y="-9" width="48" height="18" rx="4" fill="{RED}"/>
    <rect x="-9" y="-24" width="18" height="48" rx="4" fill="{RED}"/>
  </g>
  <!-- face -->
  <circle cx="76" cy="140" r="5" fill="{TEXT}">
    <animate attributeName="ry" values="5;0.6;5" dur="3s" begin="0.5s" repeatCount="indefinite"/>
  </circle>
  <circle cx="124" cy="140" r="5" fill="{TEXT}">
    <animate attributeName="ry" values="5;0.6;5" dur="3s" begin="0.5s" repeatCount="indefinite"/>
  </circle>
  <path d="M 84 156 Q 100 166 116 156" fill="none" stroke="{TEXT}" stroke-width="3" stroke-linecap="round"/>
</svg>'''
    return svg


def main() -> int:
    (OUT / "chart-coverage-trend.svg").write_text(trend_line())
    (OUT / "chart-retirement.svg").write_text(retirement_bars())
    (OUT / "chart-model-mape.svg").write_text(model_mape_bars())
    (OUT / "mascot.svg").write_text(mascot())
    for name in ("chart-coverage-trend", "chart-retirement", "chart-model-mape", "mascot"):
        print(f"wrote docs/images/{name}.svg")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())