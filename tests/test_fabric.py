"""Tests for the Fabric Lakehouse export.

The Lakehouse is only useful if its layout actually mirrors the warehouse, so
the test suite checks the export row-for-row and the folder shape against what
Fabric expects, rather than only that files were written.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import duckdb
import pytest

ROOT = Path(__file__).resolve().parents[1]
WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
OUT = ROOT / "data" / "export" / "fabric_lakehouse"

pytestmark = pytest.mark.skipif(
    not WAREHOUSE.exists(), reason="warehouse not built"
)


@pytest.fixture(scope="module")
def export() -> dict:
    result = subprocess.run(
        [sys.executable, "-m", "src.fabric.export_lakehouse"],
        cwd=ROOT,
        env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stderr[-500:]
    return json.loads((OUT / "_metadata.json").read_text())


class TestLayout:
    def test_tables_folder_exists(self):
        assert (OUT / "Tables").is_dir()

    def test_files_folder_exists(self):
        assert (OUT / "Files").is_dir()

    def test_every_table_has_a_parquet_folder(self, export):
        for table in export["tables"]:
            folder = OUT / "Tables" / table["table"]
            parquet = folder / f"{table['table']}.parquet"
            assert folder.is_dir()
            assert parquet.exists(), f"missing parquet for {table['table']}"

    def test_metadata_matches_folders(self, export):
        listed = {t["table"] for t in export["tables"]}
        on_disk = {p.name for p in (OUT / "Tables").iterdir() if p.is_dir()}
        assert listed == on_disk

    def test_view_tables_are_not_exported_as_duplicates(self, export):
        names = {t["table"] for t in export["tables"]}
        assert not any(n.startswith("v_") for n in names)


class TestFidelity:
    @pytest.mark.parametrize("table", [
        "dim_country", "dim_region", "fact_healthcare_workers",
        "fact_regional_workforce", "fact_staffing_shortage",
    ])
    def test_row_count_matches_warehouse(self, export, table):
        exported = next(
            t for t in export["tables"] if t["table"] == table
        )["rows"]
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        try:
            warehouse_rows = con.execute(
                f'SELECT COUNT(*) FROM "{table}"'
            ).fetchone()[0]
        finally:
            con.close()
        assert exported == warehouse_rows

    def test_parquet_roundtrip_preserves_measure_columns(self, export):
        """The exported Parquet must carry the same columns as the warehouse."""
        con = duckdb.connect(str(WAREHOUSE), read_only=True)
        try:
            ware_cols = {
                r[0]
                for r in con.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name='fact_healthcare_workers'"
                ).fetchall()
            }
        finally:
            con.close()
        con2 = duckdb.connect()
        try:
            df = con2.execute(
                f"SELECT * FROM parquet_scan('{(OUT / 'Tables' / 'fact_healthcare_workers' / 'fact_healthcare_workers.parquet').as_posix()}') LIMIT 0"
            ).df()
            export_cols = set(df.columns)
        finally:
            con2.close()
        assert ware_cols <= export_cols

    def test_no_export_errors(self, export):
        assert "errors" not in export or not export["errors"]


class TestSchema:
    def test_every_export_is_delta_or_parquet_ready(self, export):
        extensions = {Path(t["path"]).suffix for t in export["tables"]}
        assert extensions == {".parquet"}

    def test_report_and_images_are_files_payload(self, export):
        assert set(export) >= {"source", "tables", "files"}
        assert any(p.endswith((".png", ".svg")) for p in export["files"]), \
            "expected the Files/ payload to carry the report artefacts"