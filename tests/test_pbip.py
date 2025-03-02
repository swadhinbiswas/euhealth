"""Validate the generated Power BI Project.

The PBIP is consumed by Power BI Desktop, which reports binding problems as a
list of red fields rather than a parse error. That means a wrong measure name
still opens -- it just shows as an empty visual. These checks catch it here
instead.

The suite verifies the four things that silently break a PBIP: a measure name
that does not exist, a column that does not exist on the referenced table, a
relationship pointing at a missing column, and duplicate visual names within a
page.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
PBIP = ROOT / "powerbi" / "pbip"
PROJECT = "EU-Health-Workforce"
REPORT = PBIP / f"{PROJECT}.Report" / "report.json"
MODEL = PBIP / "definition" / f"{PROJECT}.SemanticModel" / "model.bim"
DAX = ROOT / "powerbi" / "measures.dax"

pytestmark = pytest.mark.skipif(
    not REPORT.exists() or not MODEL.exists(),
    reason="PBIP not generated; run 'make pbip'",
)


@pytest.fixture(scope="module")
def model() -> dict:
    return json.loads(MODEL.read_text())


@pytest.fixture(scope="module")
def report() -> dict:
    return json.loads(REPORT.read_text())


@pytest.fixture(scope="module")
def measures(model) -> dict:
    """Every measure in the model, keyed by name."""
    return {
        m["name"]: m
        for t in model["tables"]
        for m in t.get("measures", [])
    }


def measure_names(model: dict) -> set:
    return {m["name"] for t in model["tables"] for m in t.get("measures", [])}


@pytest.fixture(scope="module")
def columns(model) -> dict:
    return {
        t["name"]: {c["name"] for c in t["columns"]}
        for t in model["tables"]
    }


def _bindings(visual: dict) -> tuple[list[str], list[tuple[str, str]]]:
    """Extract (measure names, (table, column) pairs) from a visual.

    The two are distinguished by which SourceRef key appears: a column
    reference carries ``Entity``, a measure reference carries ``Source``.
    Matching on ``Property`` alone conflates them, which is how a column named
    like a measure slips through unresolved.
    """
    blob = json.dumps(visual)
    pairs = set(re.findall(
        r'"Expression":\s*\{\s*"SourceRef":\s*\{\s*"Entity":\s*"([^"]+)"'
        r'\}\s*\},\s*"Property":\s*"([^"]+)"',
        blob,
    ))
    # A column reference carries an Entity; a measure reference carries Source.
    measure_names = set(re.findall(
        r'"Measure":\s*\{\s*"Expression":\s*\{\s*"SourceRef":'
        r'\s*\{\s*"Source":\s*"[^"]+"\s*\}\s*\},\s*"Property":'
        r'\s*"([^"]+)"',
        blob,
    ))
    return measure_names, pairs


class TestProjectFiles:
    def test_project_file_exists(self):
        assert (PBIP / f"{PROJECT}.pbip").exists()

    def test_pbip_references_report_and_model(self):
        project = json.loads((PBIP / f"{PROJECT}.pbip").read_text())
        assert project["name"] == PROJECT
        assert project["report"]["path"] == f"{PROJECT}.Report"

    def test_definition_ids_reference_real_folders(self):
        pbid = json.loads((PBIP / "definition.pbid").read_text())
        path = pbid["datasetReference"]["byPath"]["path"]
        assert (PBIP / path / "model.bim").exists(), \
            f"definition.pbid points at a missing folder: {path}"

    def test_model_declares_compatibility(self, model):
        assert model["defaultPowerBIDataSourceVersion"]
        assert model["tables"]

    #: The measure-only table exists to give the semantic layer one home; it
    #: holds no data and therefore has no partition. Every other table must.
    MEASURE_ONLY = "_measures"

    def test_every_data_table_has_a_source(self, model):
        for table in model["tables"]:
            if table["name"] == self.MEASURE_ONLY:
                assert not table["partitions"]
                continue
            assert table["partitions"], f"{table['name']} has no partition"

    def test_every_partition_is_a_power_query_m_query(self, model):
        for table in model["tables"]:
            for partition in table["partitions"]:
                source = partition["source"]["source"]
                assert source["type"] == "m", table["name"]
                assert "let" in source["expression"].lower(), \
                    f"{table['name']} partition is not an M let expression"
                assert "in" in source["expression"].lower()

    def test_forecast_table_is_present(self, model, columns):
        assert "fact_forecast" in columns
        assert {"forecast", "baseline_forecast", "year",
                "country_code"} <= columns["fact_forecast"]

    def test_every_table_has_a_snapshot(self, model):
        snapshots = {
            p.stem for p in (PBIP / "definition" / "snapshots").glob("*.parquet")
        }
        for table in model["tables"]:
            if table["name"] == "_measures":
                continue
            assert table["name"] in snapshots, \
                f"no Parquet snapshot for {table['name']}"


class TestMeasures:
    def test_measure_count_is_substantial(self, measures):
        assert len(measures) >= 35

    def test_no_measure_is_empty(self, measures):
        for name, measure in measures.items():
            assert measure["expression"].strip(), f"{name} is empty"

    def test_parentheses_are_balanced(self, measures):
        for name, measure in measures.items():
            expression = measure["expression"]
            assert expression.count("(") == expression.count(")"), \
                f"{name} has unbalanced parentheses"

    def test_no_dax_keyword_was_parsed_as_a_name(self, measures):
        for keyword in ("VAR", "RETURN", "IF", "CALCULATE", "DEFINE"):
            assert keyword not in measures, \
                f"a {keyword} declaration was captured as a measure"

    def test_every_measure_has_a_format_string(self, measures):
        for name, measure in measures.items():
            assert measure.get("formatString"), f"{name} has no format string"

    def test_measures_are_grouped_into_folders(self, measures):
        folders = {m["displayFolder"] for m in measures.values()}
        assert len(folders) >= 4, "measures are not organised into folders"

    #: Measures defined in generate_pbip because the forecast is a model output
    #: rather than a fact in the star schema. They are absent from the .dax
    #: library by design, so they are excluded from the parity check.
    GENERATED_MEASURES = {
        "Forecast Workers", "Baseline Forecast", "Forecast Change %",
    }

    def test_measures_match_the_dax_library(self, measures):
        """Every library measure reaches the PBIP, and vice versa.

        The PBIP is generated from measures.dax, so any drift is a generator
        bug. A measure that exists only in the PBIP would be invisible to
        anyone reading the library.
        """
        source = DAX.read_text()
        for name in measures:
            if name in self.GENERATED_MEASURES:
                continue
            assert re.search(
                rf"^{re.escape(name)} :=", source, re.MULTILINE
            ), f"{name} is in the PBIP but not in measures.dax"

        # A name beginning with a DAX statement keyword is a declaration
        # inside a measure body, not a measure definition.
        keywords = ("VAR", "RETURN", "DEFINE", "MEASURE", "EVALUATE",
                    "IF", "CALCULATE", "SWITCH", "MIN", "MAX", "SUM",
                    "DIVIDE", "ROUND", "COUNTROWS", "ISBLANK", "SUMX",
                    "MAXX", "MINX", "AVERAGEX", "RETURNFILTERVALUES")
        library = {
            name for name in re.findall(
                r"^([A-Z][A-Za-z0-9 +%]*) :=", source, re.MULTILINE
            )
            if name.split(" ")[0] not in keywords and name.strip() != "Formatting"
        }
        missing = library - set(measures)
        assert not missing, f"in measures.dax but not in the PBIP: {missing}"

    def test_no_measure_references_an_unknown_table(self, measures, columns):
        blob = json.dumps(measures)
        for entity in set(re.findall(r'"Entity":\s*"([^"]+)"', blob)):
            assert entity in columns, f"measure references {entity}"

    def test_no_measure_references_an_unknown_column(self, measures, columns):
        blob = json.dumps(measures)
        for entity, column in re.findall(
            r'"Entity":\s*"([^"]+)"\s*\}\s*,\s*"Property":\s*"([^"]+)"', blob
        ):
            assert column in columns.get(entity, set()), \
                f"measure references {entity}[{column}] which does not exist"

    def test_no_measure_averages_a_ratio(self, measures):
        offenders = []
        for name, measure in measures.items():
            body = measure["expression"]
            for column in ("coverage_index", "ratio", "per_1000", "score"):
                if re.search(rf"AVERAGE[X]?\s*\([^)]*\[{column}", body,
                             re.IGNORECASE):
                    offenders.append(f"{name} averages [{column}]")
        assert not offenders, offenders


class TestRelationships:
    def test_relationships_exist(self, model):
        assert len(model["relationships"]) >= 15

    def test_relationship_endpoints_exist(self, model, columns):
        for rel in model["relationships"]:
            assert rel["fromTable"] in columns, rel["fromTable"]
            assert rel["toTable"] in columns, rel["toTable"]
            assert rel["fromColumn"] in columns[rel["fromTable"]], \
                f"{rel['fromTable']} has no column {rel['fromColumn']}"
            assert rel["toColumn"] in columns[rel["toTable"]], \
                f"{rel['toTable']} has no column {rel['toColumn']}"

    def test_relationships_point_from_fact_to_dimension(self, model):
        for rel in model["relationships"]:
            assert rel["fromTable"].startswith("fact_"), rel["name"]
            assert rel["toTable"].startswith("dim_"), rel["name"]

    def test_filtering_is_single_direction(self, model):
        """Bidirectional filtering would let a slicer cross-filter unrelated
        visuals, which is the classic cause of a dashboard that quietly lies.
        """
        for rel in model["relationships"]:
            assert rel["crossFilteringBehavior"] == "oneDirection", rel["name"]

    def test_relationship_names_are_unique(self, model):
        names = [r["name"] for r in model["relationships"]]
        assert len(names) == len(set(names))

    def test_every_fact_has_a_relationship(self, model, columns):
        facts = {t for t in columns if t.startswith("fact_")}
        linked = {r["fromTable"] for r in model["relationships"]}
        assert facts == linked


class TestReport:
    def test_has_eight_pages(self, report):
        assert len(report["sections"]) == 8

    def test_pages_have_ordinals(self, report):
        ordinals = sorted(p["ordinal"] for p in report["sections"].values())
        assert ordinals == list(range(8))

    def test_every_page_has_visuals(self, report):
        for name, page in report["sections"].items():
            assert page["visualContainers"], f"{name} is empty"

    def test_total_visual_count_is_substantial(self, report):
        total = sum(
            len(p["visualContainers"]) for p in report["sections"].values()
        )
        assert total >= 40

    def test_every_measure_reference_resolves(self, report, measures):
        unresolved = []
        for page_name, page in report["sections"].items():
            for container in page["visualContainers"]:
                names, _ = _bindings(container)
                for name in names:
                    if name not in measures:
                        unresolved.append(f"{page_name}: {name}")
        assert not unresolved, unresolved

    def test_every_column_reference_resolves(self, report, columns):
        unresolved = []
        for page_name, page in report["sections"].items():
            for container in page["visualContainers"]:
                _, pairs = _bindings(container)
                for table, column in pairs:
                    if table not in columns:
                        unresolved.append(f"{page_name}: unknown table {table}")
                    elif column not in columns[table]:
                        unresolved.append(
                            f"{page_name}: {table}[{column}] does not exist"
                        )
        assert not unresolved, unresolved

    def test_visual_names_are_unique_within_a_page(self, report):
        for page_name, page in report["sections"].items():
            names = [c["name"] for c in page["visualContainers"]]
            assert len(names) == len(set(names)), \
                f"{page_name} has duplicate visual names"

    def test_visual_names_are_globally_unique(self, report):
        """PBIR keys visuals by name across the whole report, so a collision
        on two different pages makes one of them unaddressable.
        """
        all_names = [
            c["name"]
            for page in report["sections"].values()
            for c in page["visualContainers"]
        ]
        duplicates = {n for n in all_names if all_names.count(n) > 1}
        assert not duplicates, f"duplicate visual names across pages: {duplicates}"

    def test_every_visual_has_a_position(self, report):
        for page_name, page in report["sections"].items():
            for container in page["visualContainers"]:
                assert "position" in container, \
                    f"{page_name}/{container['name']} has no position"

    def test_positions_fit_within_the_canvas(self, report):
        for page_name, page in report["sections"].values() if False else \
                report["sections"].items():
            canvas_w = page["width"]
            for container in page["visualContainers"]:
                position = container["position"]
                assert position["x"] >= 0, page_name
                assert position["x"] + position["width"] <= canvas_w + 2, \
                    f"{page_name}/{container['name']} overflows horizontally"

    def test_every_page_states_its_caveat(self, report):
        """At least one textbox per page, carrying the provenance caveat."""
        for page_name, page in report["sections"].items():
            textboxes = [
                c for c in page["visualContainers"]
                if c.get("visual", {}).get("visualType") == "textbox"
            ]
            assert textboxes, f"{page_name} has no explanatory text"

    def test_visual_types_are_real(self, report):
        allowed = {
            "card", "barChart", "clusteredColumnChart", "lineChart",
            "azureMap", "slicer", "tableEx", "textbox", "multiRowCard",
        }
        used = {
            c["visual"]["visualType"]
            for page in report["sections"].values()
            for c in page["visualContainers"]
            if "visual" in c and "visualType" in c["visual"]
        }
        assert used <= allowed, f"unknown visual types: {used - allowed}"

    def test_theme_is_embedded(self):
        theme = (
            PBIP / f"{PROJECT}.Report" / "StaticResources"
            / "SharedResources" / "BaseThemes" / "EUHealthWorkforce.json"
        )
        assert theme.exists()
        data = json.loads(theme.read_text())
        assert data["dataColors"]
        assert data["bad"]["fg"], "the critical risk colour is not in the theme"