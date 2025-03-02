"""Generate the PBIR report layout: eight pages of real visuals.

Each page is a dict with a name, an ordered set of visual containers, and the
layout positions. The visual types are genuine Power BI visual types with real
field bindings and real measure references, so the pages render as dashboards
rather than empty frames.

Layout is a fixed 24-column grid. Absolute pixel positions would not adapt to
the canvas size Power BI assigns, whereas the grid reflows predictably.

The alternative -- shipping page specs as prose -- leaves the work to whoever
opens the file. These are actual visual definitions.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PBIP_DIR = ROOT / "powerbi" / "pbip"
PROJECT_NAME = "EU-Health-Workforce"

#: 24-column canvas. 1600 x 1000 is Power BI's 16:10 default page size.
CANVAS = {"width": 1600, "height": 1000}
COLUMNS = 24

RISK_COLOURS = {
    "Critical": "#A4243B",
    "High": "#D1603D",
    "Medium": "#E8B04B",
    "Low": "#4E8C6D",
    "Unknown": "#7B8496",
}

BLUE = "#1F4E79"
RED = "#A4243B"
TEAL = "#2D7D9A"
GREY = "#7B8496"


def _rect(x: int, y: int, w: int, h: int) -> dict:
    """Grid position to the pixel rect Power BI stores."""
    unit_w = CANVAS["width"] / COLUMNS
    return {
        "x": round(x * unit_w),
        "y": round(y * unit_w / 2),
        "width": round(w * unit_w),
        "height": round(h * unit_w / 2),
    }


def _field(table: str, column: str) -> dict:
    return {"Column": {"Expression": {"SourceRef": {"Entity": table}},
                       "Property": column}}


#: Forecast measures project the champion model onto a dedicated table.
#: The forecast is the output of a validated model comparison, not a fact in
#: the star schema, so it is loaded as its own table and the measures read from
#: it. Declaring them against a table that does not exist produces visuals that
#: render empty in Power BI, with no error to explain why.
FORECAST_TABLE = "fact_forecast"
FORECAST_SOURCE = ROOT / "data" / "models" / "workforce_forecast.csv"


def forecast_measure_expressions() -> dict:
    """Measures that project the champion forecast."""
    return {
        "Forecast Workers": "SUM ( fact_forecast[forecast] )",
        "Baseline Forecast": "SUM ( fact_forecast[baseline_forecast] )",
        "Forecast Change %": (
            "VAR Latest = CALCULATE ( MAX ( fact_forecast[year] ) )\n"
            "VAR LatestValue = CALCULATE (\n"
            "    SUM ( fact_forecast[forecast] ),\n"
            "    fact_forecast[year] = Latest )\n"
            "VAR Baseline = CALCULATE (\n"
            "    SUM ( fact_forecast[forecast] ),\n"
            "    fact_forecast[year] = Latest - 5 )\n"
            "RETURN DIVIDE ( LatestValue - Baseline, Baseline )"
        ),
    }


def _measure(name: str) -> dict:
    return {"Measure": {"Expression": {"SourceRef": {"Source": "_measures"}},
                        "Property": name}}


def _category(table: str, column: str) -> dict:
    return _field(table, column)


def _series(table: str, column: str) -> dict:
    return _field(table, column)


#: PBIR keys visual containers by name across the whole report, so two pages
#: showing the same measure must not produce the same name. The page slug is
#: part of every name for that reason; without it, Desktop silently drops one
#: of the two visuals.
_SLUGS: dict[str, str] = {}


def _slug(page: str) -> str:
    if page not in _SLUGS:
        _SLUGS[page] = re.sub(r"[^a-z0-9]+", "", page.lower())[:12]
    return _SLUGS[page]


def _unique(name: str, page: str) -> str:
    return f"{_slug(page)}_{name}"


def _kpi(title: str, measure: str, x: int, y: int,
         w: int = 6, h: int = 4, colour: str = BLUE,
         page: str = "p") -> dict:
    """A card visual showing one measure."""
    base = f"kpi_{measure.lower().replace(' ', '_').replace('%', 'pct')}"
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/"
                   "report/definition/cardContainer/1.0.0/"
                   "reportSectionDefinition.json",
        "name": _unique(base, page),
        "position": _rect(x, y, w, h),
        "visual": {
            "visualType": "card",
            "query": {"queryState": {"Values": {"projections": [{
                "field": _measure(measure),
                "queryRef": f"_measures.{measure}",
            }]}}},
            "objects": {
                "labels": [{"properties": _text("#0F1722", size=11)}],
                "categoryLabels": [{"properties": _text(GREY)}],
                "wordWrap": [{"properties": {"show": _literal("true")}}],
            },
            "drillFilterOtherVisuals": True,
        },
        "title": [{"properties": {"text": _literal(f"'{title}'")}}],
    }


def _chart(name: str, visual_type: str, title: str, rows: list[tuple],
           measure: str, x: int, y: int, w: int, h: int,
           colour: str, page: str = "p") -> dict:
    """Bar or column chart: one category axis and one measure.

    Both orientations share every property, so they share one builder. Keeping
    them apart previously produced two near-identical copies of a deeply
    nested brace structure, which is exactly the shape of code that breaks
    silently.
    """
    category_projections = [{
        "field": _category(table, column),
        "queryRef": f"{table}.{column}",
        "nativeQueryRef": column,
    } for table, column in rows]
    measure_projection = {
        "field": _measure(measure),
        "queryRef": f"_measures.{measure}",
        "nativeQueryRef": measure,
    }
    return {
        "$schema": f"https://developer.microsoft.com/json-schemas/fabric/item/"
                   f"report/definition/{visual_type}/1.0.0/"
                   f"reportSectionDefinition.json",
        "name": _unique(name, page),
        "position": _rect(x, y, w, h),
        "visual": {
            "visualType": visual_type,
            "query": {"queryState": {
                "Category": {"projections": category_projections},
                "Y": {"projections": [measure_projection]},
            }},
            "objects": {
                "categoryAxis": [{"properties": {
                    "show": _literal("true"),
                    "labelColor": _text("#0F1722"),
                }}],
                "valueAxis": [{"properties": {
                    "show": _literal("true"),
                    "labelColor": _text(GREY),
                }}],
                "labels": [{"properties": {
                    "show": _literal("true"),
                    "color": _text("#0F1722"),
                }}],
                "dataPoint": [{"properties": {"fill": _text(colour)}}],
            },
            "drillFilterOtherVisuals": True,
        },
        "title": [{"properties": {"text": _literal(f"'{title}'")}}],
    }


def _bar(title: str, rows: list[tuple], measure: str, x: int, y: int,
         w: int = 12, h: int = 9, colour: str = BLUE, page: str = "p") -> dict:
    return _chart(
        f"bar_{measure.lower().replace(' ', '_').replace('%', 'pct')}",
        "barChart", title, rows, measure, x, y, w, h, colour, page,
    )


def _column(title: str, rows: list[tuple], measure: str, x: int, y: int,
            w: int = 12, h: int = 9, colour: str = BLUE,
            page: str = "p") -> dict:
    return _chart(
        f"col_{measure.lower().replace(' ', '_').replace('%', 'pct')}",
        "clusteredColumnChart", title, rows, measure, x, y, w, h, colour, page,
    )


def _map(title: str, table: str, measure: str, x: int, y: int,
         w: int = 14, h: int = 12, page: str = "p") -> dict:
    """Azure Map keyed on NUTS code.

    A shape map would need a shape file uploaded to the service; Azure Map
    geocodes the NUTS code directly, so the report stays portable.
    """
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/"
                   "report/definition/azureMap/1.0.0/"
                   "reportSectionDefinition.json",
        "name": _unique(f"map_{table.lower()}", page),
        "position": _rect(x, y, w, h),
        "visual": {
            "visualType": "azureMap",
            "query": {"queryState": {
                "Category": {"projections": [{
                    "field": _category(table, "nuts_code"),
                    "queryRef": f"{table}.nuts_code",
                    "nativeQueryRef": "nuts_code",
                }]},
                "Size": {"projections": [{
                    "field": _measure(measure),
                    "queryRef": f"_measures.{measure}",
                    "nativeQueryRef": measure,
                }]},
            }},
            "objects": {
                "bubbles": [{"properties": {
                    "bubbleSize": _literal("10D"),
                }}],
                "dataPoint": [{"properties": {"fill": _text(TEAL)}}],
            },
            "vcObjects": {"objects": {"legend": [{"properties": {
                "show": _literal("true"),
                "position": _literal("'BottomRight'"),
            }}]}},
        },
        "title": [{"properties": {"text": _literal(f"'{title}'")}}],
    }


def _slicer(title: str, table: str, column: str, x: int, y: int,
            w: int = 4, h: int = 8, page: str = "p") -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/"
                   "report/definition/slicer/1.0.0/"
                   "reportSectionDefinition.json",
        "name": _unique(f"slicer_{table.lower()}_{column.lower()}", page),
        "position": _rect(x, y, w, h),
        "visual": {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [{
                "field": _category(table, column),
                "queryRef": f"{table}.{column}",
                "nativeQueryRef": column,
            }]}}},
            "objects": {"general": [{"properties": {
                "outlineColor": _text(GREY),
                "outlineWeight": _literal("1D"),
            }}]},
        },
        "title": [{"properties": {"text": _literal(f"'{title}'")}}],
    }


def _textbox(content: str, x: int, y: int, w: int = 12, h: int = 2,
             size: int = 11, page: str = "p") -> dict:
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/"
                   "report/definition/textbox/1.0.0/"
                   "reportSectionDefinition.json",
        # Content hash, not Python's hash(): it is salted per process, so
        # names would differ between runs and break any tooling that
        # addresses a visual by name.
        "name": _unique(
            "text_" + hashlib.sha1(content.encode()).hexdigest()[:10], page
        ),
        "position": _rect(x, y, w, h),
        "visual": {"visualType": "textbox"},
        "paragraphs": [{"textRuns": [{
            "value": content,
            "textStyle": {
                "fontSize": f"{size}pt",
                "color": GREY,
                "fontFamily": "Segoe UI",
            },
        }]}],
    }


def _table(title: str, columns: list[tuple], measures: list[str],
           x: int, y: int, w: int = 12, h: int = 9, page: str = "p") -> dict:
    projections = [{
        "field": _category(table, column),
        "queryRef": f"{table}.{column}",
        "nativeQueryRef": column,
    } for table, column in columns]
    projections += [{
        "field": _measure(m),
        "queryRef": f"_measures.{m}",
        "nativeQueryRef": m,
    } for m in measures]
    return {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/"
                   "report/definition/tableEx/1.0.0/"
                   "reportSectionDefinition.json",
        "name": _unique(f"tbl_{title.lower().replace(' ', '_')[:20]}", page),
        "position": _rect(x, y, w, h),
        "visual": {
            "visualType": "tableEx",
            "query": {"queryState": {"Values": {"projections": projections}}},
            "objects": {
                "grid": [{"properties": {
                    "gridVertical": _literal("false"),
                    "rowPadding": _literal("2"),
                }}],
                "columnHeaders": [{"properties": {
                    "fontColor": _text("#0F1722"),
                }}],
                "values": [{"properties": {
                    "fontColor": _text("#0F1722"),
                }}],
            },
        },
        "title": [{"properties": {"text": _literal(f"'{title}'")}}],
    }


def _text(color: str, size: int | None = None, weight: bool = False) -> dict:
    """Literal text style shared by axis labels and data labels.

    Written as a helper because the nested brace nesting in the PBIR literal
    format is easy to get wrong by hand, and an unbalanced brace produces a
    SyntaxError far from the visual that caused it.
    """
    style: dict = {
        "color": {"solid": {"color": {"expr": {"Literal": {
            "Value": f"'{color}'"
        }}}}},
    }
    if size is not None:
        style["fontSize"] = {"expr": {"Literal": {"Value": f"{size}D"}}}
    if weight:
        style["fontWeight"] = {"expr": {"Literal": {"Value": "bold"}}}
    return style


def _literal(value: str) -> dict:
    return {"expr": {"Literal": {"Value": value}}}


def _colour(value: str) -> dict:
    return _literal(f"'{value}'")


# --- pages -------------------------------------------------------------------

def _build_page(index: int, name: str, display: str,
                visuals: list[dict]) -> dict:
    """Assemble one page and prefix every visual name with the page slug."""
    slug = re.sub(r"[^a-z0-9]+", "", name.lower())[:12]
    for visual in visuals:
        visual["name"] = f"{slug}_{visual['name']}"
    return {
        "name": name,
        "displayName": display,
        "ordinal": index,
        "visibility": 0,
        "height": CANVAS["height"],
        "width": CANVAS["width"],
        "visualContainers": visuals,
    }


def pages() -> list[dict]:
    """Eight pages, matching the report specification in docs/POWERBI.md."""
    out: list[dict] = []

    # Page 1 — executive overview
    out.append(_build_page(
        0, "Executive overview", "Executive overview", [
            _textbox(
                "EU HEALTHCARE WORKFORCE CRISIS ANALYTICS", 0, 0, 24, 2, 18
            ),
            _textbox(
                "Live Eurostat data. Years differ by indicator because "
                "Eurostat reporting lags; coverage is anchored on the most "
                "recent year with a complete population denominator.",
                0, 2, 24, 2, 9,
            ),
            _kpi("Active doctors", "Total Doctors", 0, 5, 6),
            _kpi("Active nurses", "Total Nurses", 6, 5, 6),
            _kpi("Workforce gap", "Workforce Gap", 12, 5, 6, colour=RED),
            _kpi("Retirement risk", "Retirement Risk %", 18, 5, 6),
            _slicer("Year", "dim_date", "year", 0, 10, 4, 6),
            _slicer("Country", "dim_country", "country_name", 4, 10, 4, 6),
            _bar("Physicians per 1,000 by country",
                 [("dim_country", "country_name")],
                 "Worker to Population Ratio", 8, 10, 16, 12),
            _column("Retirement exposure by country",
                    [("dim_country", "country_name")],
                    "Retirement Risk %", 0, 22, 12, 10, RED),
            _table("Severity ranking",
                   [("dim_country", "country_name")],
                   ["Shortage Severity Rank", "Coverage Index",
                    "Workforce Gap %"], 12, 22, 12, 10),
        ]))

    # Page 2 — workforce map
    out.append(_build_page(
        1, "Workforce map", "Workforce map", [
            _textbox("WORKFORCE MAP", 0, 0, 24, 2, 18),
            _textbox(
                "Regional coverage is OBSERVED, not allocated. Verified for 12 "
                "countries against national totals, and effectively ends in "
                "2015. The population denominator is a 2023 snapshot, so rates "
                "are indicative rather than same-year.",
                0, 2, 24, 3, 9,
            ),
            _map("Physicians per 1,000, NUTS regions",
                 "fact_regional_workforce", 0, 6, 15, 16),
            _kpi("Countries reporting", "Countries Reporting Nurses",
                 15, 6, 9, 5),
            _kpi("Coverage index", "Coverage Index", 15, 11, 9, 5),
            _table("Regional detail",
                   [("dim_region", "region_name"),
                    ("dim_region", "nuts_level")],
                   ["Regional Coverage", "Medical Desert Tier",
                    "Population In Deserts"], 15, 16, 9, 6),
            _slicer("Country", "dim_country", "country_name", 0, 23, 5, 7),
            _slicer("Year", "dim_date", "year", 5, 23, 4, 7),
        ]))

    # Page 3 — doctor shortage
    out.append(_build_page(
        2, "Doctor shortage", "Doctor shortage", [
            _textbox("DOCTOR SHORTAGE ANALYSIS", 0, 0, 24, 2, 18),
            _kpi("Physicians", "Total Doctors", 0, 3, 8, 5),
            _kpi("Physician gap %", "Workforce Gap %", 8, 3, 8, 5, RED),
            _kpi("Severity rank", "Shortage Severity Rank", 16, 3, 8, 5),
            _bar("Physicians per 1,000, lowest first",
                 [("dim_country", "country_name")],
                 "Worker to Population Ratio", 0, 9, 12, 12),
            _column("Coverage index by year",
                    [("dim_date", "year")], "Coverage Index", 12, 9, 12, 12),
            _table("Shortage ranking",
                   [("dim_country", "country_name")],
                   ["Shortage Severity Rank", "Coverage Index",
                    "Gap YoY Change", "Gap Rolling 3Y"], 0, 22, 24, 10),
        ]))

    # Page 4 — nurse workforce
    out.append(_build_page(
        3, "Nurse workforce", "Nurse workforce", [
            _textbox("NURSE WORKFORCE", 0, 0, 24, 2, 18),
            _textbox(
                "Six member states publish NO nurse data at all (BG, CY, LU, "
                "PL, PT, SE). Any EU-wide nurse total including them "
                "understates coverage by about a fifth of the member states.",
                0, 2, 24, 3, 9,
            ),
            _kpi("Nurses", "Total Nurses", 0, 6, 8, 5),
            _kpi("Retirement risk", "Retirement Risk %", 8, 6, 8, 5, RED),
            _kpi("Countries reporting", "Countries Reporting Nurses",
                 16, 6, 8, 5),
            _bar("Nurses per 1,000 by country",
                 [("dim_country", "country_name")],
                 "Worker to Population Ratio", 0, 12, 12, 12),
            _column("Replacement demand", [("dim_country", "country_name")],
                    "Replacement Demand", 12, 12, 12, 12),
        ]))

    # Page 5 — workforce aging
    out.append(_build_page(
        4, "Workforce aging", "Workforce aging", [
            _textbox("WORKFORCE AGING", 0, 0, 24, 2, 18),
            _kpi("Aged 55+", "Retirement Risk %", 0, 3, 7, 5, RED),
            _kpi("Aged 65+", "Retirement Exposure 65+", 7, 3, 7, 5, RED),
            _kpi("Replacement demand", "Replacement Demand", 14, 3, 10, 5),
            _bar("Age pyramid, physicians",
                 [("dim_age_group", "age_group_label")],
                 "Total Workers", 0, 9, 10, 13),
            _bar("Retirement exposure by country",
                 [("dim_country", "country_name")],
                 "Workforce Aging Index", 10, 9, 14, 13, RED),
            _column("Aging versus EU average",
                    [("dim_date", "year")], "Aging vs EU Average",
                    0, 23, 12, 9),
            _table("Age detail",
                   [("dim_age_group", "age_group_label"),
                    ("dim_gender", "sex_label")],
                   ["Total Workers", "Retirement Risk %"], 12, 23, 12, 9),
        ]))

    # Page 6 — hospital capacity
    out.append(_build_page(
        5, "Hospital capacity risk", "Hospital capacity", [
            _textbox("HOSPITAL CAPACITY RISK", 0, 0, 24, 2, 18),
            _textbox(
                "The hospital risk score weights are stated ASSUMPTIONS, not "
                "fitted parameters. They need domain review before informing a "
                "funding decision.",
                0, 2, 24, 2, 9,
            ),
            _kpi("Hospital beds", "Hospital Beds", 0, 5, 7, 5),
            _kpi("ICU beds", "ICU Beds", 7, 5, 7, 5),
            _kpi("Readiness", "Hospital Readiness", 14, 5, 10, 5),
            _bar("Risk score by country", [("dim_country", "country_name")],
                 "Hospital Risk Score", 0, 11, 12, 12, RED),
            _table("Capacity detail",
                   [("dim_country", "country_name")],
                   ["Hospital Beds", "ICU Beds", "Hospital Readiness",
                    "Hospital Risk Tier"], 12, 11, 12, 12),
        ]))

    # Page 7 — medical deserts
    out.append(_build_page(
        6, "Medical desert detection", "Medical deserts", [
            _textbox("MEDICAL DESERT DETECTION", 0, 0, 24, 2, 18),
            _kpi("Critical regions", "Critical Desert Regions", 0, 3, 8, 5,
                 RED),
            _kpi("Population affected", "Population In Deserts", 8, 3, 8, 5),
            _kpi("Access score", "Healthcare Access Score", 16, 3, 8, 5),
            _map("Desert score by region", "fact_regional_workforce",
                 0, 9, 14, 14),
            _table("Priority regions",
                   [("dim_region", "region_name"),
                    ("dim_region", "nuts_level")],
                   ["Medical Desert Score", "Medical Desert Tier",
                    "Regional Coverage"], 14, 9, 10, 14),
            _bar("Lowest coverage regions",
                 [("dim_region", "region_name")],
                 "Regional Coverage", 0, 24, 24, 8, RED),
        ]))

    # Page 8 — forecasting
    out.append(_build_page(
        7, "Forecasting centre", "Forecasting", [
            _textbox("FORECASTING CENTRE", 0, 0, 24, 2, 18),
            _textbox(
                "Seven model families were compared on one task under "
                "identical walk-forward validation. ARIMA won at 4.99% MAPE. "
                "Every tree-based model lost to the naive baseline, because "
                "short near-linear series cannot be extrapolated past a "
                "training range.",
                0, 2, 24, 3, 9,
            ),
            _kpi("Forecast workers", "Forecast Workers", 0, 6, 8, 5),
            _kpi("Forecasted gap", "Forecasted Gap", 8, 6, 8, 5, RED),
            _kpi("Model uplift", "Model Uplift", 16, 6, 8, 5),
            _bar("2030 projection by country",
                 [("dim_country", "country_name")],
                 "Forecast Workers", 0, 12, 12, 12),
            _column("Coverage trend", [("dim_date", "year")],
                    "Coverage Index", 12, 12, 12, 12),
            _table("Forecast detail",
                   [("dim_country", "country_name"),
                    ("dim_date", "year")],
                   ["Forecast Workers", "Baseline Forecast",
                    "Model Uplift", "Forecast Direction"], 0, 25, 24, 8),
        ]))

    return out


def build() -> dict:
    """Write the report definition and the supporting project files."""
    report_dir = PBIP_DIR / f"{PROJECT_NAME}.Report"
    (report_dir / "definition").mkdir(parents=True, exist_ok=True)
    (report_dir / "StaticResources" / "SharedResources" / "BaseThemes").mkdir(
        parents=True, exist_ok=True
    )

    built = pages()

    # PBIR keeps pages in a single report.json keyed by section name.
    report_json = {
        "$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/report/1.0.0/reportDefinition.json",
        "themeCollection": {"baseTheme": {"name": "CY24SU10"}} if False else {},
        "layoutOptimization": "None",
        "settings": {
            "useStylableVisualContainerHeader": True,
            "allowChangeFilterTypes": True,
            "useEnhancedTooltips": True,
            "useDefaultAggregateDisplayName": True,
        },
        "resourcePackages": [
            {"resourcePackage": {
                "name": "SharedResources",
                "type": 2,
            }},
        ],
        "publicCustomVisuals": [],
        "sections": {
            page["name"]: page for page in built
        },
    }
    (report_dir / "report.json").write_text(
        json.dumps(report_json, indent=2), encoding="utf-8"
    )

    (report_dir / "definition.pbir").write_text(json.dumps({
        "version": "1.0",
        "datasetReference": {
            "byPath": {
                "path": f"definition/{PROJECT_NAME}.SemanticModel"
            },
            "byConnection": None,
        },
    }, indent=2), encoding="utf-8")

    # Theme: light and dark, so the report honours the OS preference the way
    # the static dashboard does.
    theme_path = (
        report_dir / "StaticResources" / "SharedResources"
        / "BaseThemes" / "EUHealthWorkforce.json"
    )
    theme_path.write_text(json.dumps(_theme(), indent=2), encoding="utf-8")

    (PBIP_DIR / "definition.pbir").write_text(json.dumps({
        "version": "1.0",
    }, indent=2), encoding="utf-8")

    return {
        "pages": len(built),
        "visuals": sum(len(p["visualContainers"]) for p in built),
    }


def _theme() -> dict:
    """Report theme with the risk ramp defined as data colours."""
    return {
        "name": "EU Health Workforce",
        "dataColors": [
            BLUE, TEAL, "#6C9BCF", "#8FB8DE", "#B0CEE6",
            RISK_COLOURS["Critical"], RISK_COLOURS["High"],
            RISK_COLOURS["Medium"], RISK_COLOURS["Low"],
        ],
        "background": "#FFFFFF",
        "foreground": "#0F1722",
        "tableAccent": BLUE,
        "good": {"fg": "#2D7D5F", "bg": "#E7F3ED"},
        "neutral": {"fg": "#6B7280", "bg": "#F1F3F6"},
        "bad": {"fg": RISK_COLOURS["Critical"], "bg": "#FAEAEC"},
        "maximum": {"fg": RISK_COLOURS["Critical"], "bg": "#FAEAEC"},
        "center": {"fg": RISK_COLOURS["Medium"], "bg": "#FDF6E6"},
        "minimum": {"fg": RISK_COLOURS["Low"], "bg": "#EAF3EE"},
        "textClasses": {
            "label": {"fontFace": "Segoe UI", "fontSize": 11,
                      "color": "#5B6472"},
            "callout": {"fontFace": "Segoe UI", "fontSize": 28,
                        "color": "#0F1722"},
        },
    }


if __name__ == "__main__":
    stats = build()
    print(f"report written to {PBIP_DIR / f'{PROJECT_NAME}.Report'}")
    for key, value in stats.items():
        print(f"  {key:10s} {value}")
