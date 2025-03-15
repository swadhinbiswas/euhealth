"""Export the warehouse to a Microsoft Fabric Lakehouse layout.

A Fabric **Lakehouse** stores data in two folders: ``Tables/`` (managed delta
tables, queried via Spark and T-SQL) and ``Files/`` (unstructured files). This
script writes every table in the warehouse as Parquet under ``Tables/``, with
one folder per table, which is the Parquet-shortcut layout you point a Fabric
notebooks or a shortcut at.

The export is deterministic and local. It does not need Fabric credentials to
produce a correct layout; only the upload step does.

Run:
    PYTHONPATH=src python -m src.fabric.export_lakehouse
"""
from __future__ import annotations

import json
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[2]
WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
OUT = ROOT / "data" / "export" / "fabric_lakehouse"
TABLES_DIR = OUT / "Tables"
FILES_DIR = OUT / "Files"

#: OLTP-style projections that are derived views, not physical tables, and
#: therefore not part of the warehouse itself but useful in BI.
EXTRA_TABLES = ["forecast_workforce"]


def export() -> dict:
    if not WAREHOUSE.exists():
        raise SystemExit("warehouse not built; run 'make warehouse' first")
    TABLES_DIR.mkdir(parents=True, exist_ok=True)
    FILES_DIR.mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    stats = {"tables": []}
    try:
        tables = [r[0] for r in con.execute("SHOW TABLES").fetchall()]
        real = [t for t in tables if not t.startswith("v_")]
        for t in sorted(set(real + EXTRA_TABLES)):
            try:
                folder = TABLES_DIR / t
                folder.mkdir(parents=True, exist_ok=True)
                parquet = folder / f"{t}.parquet"
                con.execute(
                    f"COPY (SELECT * FROM \"{t}\") TO '{parquet}' (FORMAT PARQUET)"
                )
                rows = con.execute(f"SELECT COUNT(*) FROM \"{t}\"").fetchone()[0]
                stats["tables"].append({"table": t, "rows": rows, "path": str(parquet)})
            except Exception as exc:
                stats.setdefault("errors", []).append(f"{t}: {exc}")

        # Land the architecture diagram and forecast comparison as unstructured
        # files, as a Lakehouse's Files/ folder is meant for.
        for f in (ROOT / "docs" / "images").glob("*"):
            import shutil
            shutil.copy2(f, FILES_DIR / f.name)

        (OUT / "_metadata.json").write_text(json.dumps({
            "source": "healthcare_dw.duckdb",
            "tables": stats["tables"],
            "files": sorted(p.name for p in FILES_DIR.iterdir()),
        }, indent=2))
    finally:
        con.close()

    print(f"export complete: {len(stats['tables'])} tables, "
          f"{len(sorted(FILES_DIR.iterdir()))} files -> {OUT}")
    return stats


if __name__ == "__main__":
    export()