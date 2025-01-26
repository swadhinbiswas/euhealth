"""Compare model families and write the forecast tables.

Usage:
    python -m src.ml.cli            # full comparison + forecasts
    python -m src.ml.cli --quick    # skip the local per-series models
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import warnings
from pathlib import Path

# Prophet/cmdstanpy log every fit at INFO and statsmodels emits convergence
# warnings on short series. Both drown the comparison table without adding
# information; the per-series fit counts printed below are the real diagnostic.
warnings.filterwarnings("ignore")
for noisy in ("cmdstanpy", "prophet", "stanio"):
    logging.getLogger(noisy).setLevel(logging.WARNING)

import duckdb
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from ml.forecasting import (  # noqa: E402
    TARGET_HORIZONS,
    all_models,
    detect_structural_breaks,
    evaluate_model,
    walk_forward_folds,
)

WAREHOUSE = ROOT / "data" / "healthcare_dw.duckdb"
OUT = ROOT / "data" / "models"
OUT.mkdir(parents=True, exist_ok=True)


def load_panel() -> pd.DataFrame:
    con = duckdb.connect(str(WAREHOUSE), read_only=True)
    try:
        return con.execute("""
            SELECT country_code, profession_code, year,
                   measure_value AS headcount
            FROM fact_healthcare_workers
            WHERE measure = 'headcount_total' AND sex_code = 'T'
              AND profession_code IN ('PHYS', 'NURS')
            ORDER BY country_code, profession_code, year
        """).df()
    finally:
        con.close()


def main(quick: bool = False) -> None:
    panel = load_panel()
    print(f"panel: {len(panel)} observations, "
          f"{panel.groupby(['country_code', 'profession_code']).ngroups} series")
    print(f"years: {panel['year'].min()}-{panel['year'].max()}")

    folds = walk_forward_folds(panel)
    print(f"walk-forward folds: {len(folds)} "
          f"(train_through, test_from, test_to)")
    for f in folds:
        print(f"   <= {f[0]}  ->  {f[1]}-{f[2]}")

    breaks = detect_structural_breaks(panel)
    if not breaks.empty:
        print(f"\nstructural breaks detected: {len(breaks)}")
        print(breaks.head(8).to_string(index=False))
        breaks.to_csv(OUT / "structural_breaks.csv", index=False)

    models = all_models()
    if quick:
        models = [m for m in models if not isinstance(
            getattr(m, "kind", ""), str) or m.kind != "randomforest"
        ]

    rows = []
    detail_frames = []
    for model in models:
        print(f"\nrunning {model.name} ...", flush=True)
        try:
            scores, detail = evaluate_model(model, panel, folds)
        except Exception as exc:
            print(f"  {model.name} FAILED: {type(exc).__name__}: {exc}")
            continue
        if not scores:
            print(f"  {model.name} produced no comparable predictions")
            continue
        rows.append({"model": model.name, **scores,
                     "n_predictions": int(len(detail)),
                     "series_fitted": getattr(model, "n_fitted", None),
                     "series_fallback": getattr(model, "n_fallback", None)})
        detail.insert(0, "model", model.name)
        detail_frames.append(detail)
        # A local model that fell back to naive on every series is not a
        # result. Report the count so it cannot be presented as one.
        fitted = getattr(model, "n_fitted", None)
        if fitted is not None:
            fallback = model.n_fallback
            total = fitted + fallback
            warning = "  [WARNING: majority fell back to naive]" \
                if total and fallback / total > 0.5 else ""
            print(f"  {model.name}: {fitted}/{total} series fitted by the "
                  f"model itself{warning}")

    comparison = pd.DataFrame(rows).sort_values("mape").reset_index(drop=True)
    print("\n" + "=" * 74)
    print("MODEL COMPARISON - walk-forward validation")
    print("=" * 74)
    print(comparison.round(3).to_string(index=False))
    comparison.to_csv(OUT / "model_comparison.csv", index=False)

    if detail_frames:
        pd.concat(detail_frames).to_csv(
            OUT / "model_predictions.csv", index=False
        )

    # --- final forecasts from the best non-trivial model --------------------
    naive = comparison[comparison["model"] == "naive"]
    if not naive.empty:
        naive_mape = float(naive["mape"].iloc[0])
        better = comparison[
            (comparison["model"] != "naive")
            & (comparison["model"] != "drift")
            & (comparison["mape"] < naive_mape)
        ]
        if better.empty:
            print("\nNo model beat the naive baseline on MAPE. Reporting the "
                  "naive forecast rather than a more complex model that "
                  "does not earn its complexity.")
            champion = "naive"
        else:
            champion = str(better.iloc[0]["model"])
    else:
        champion = str(comparison.iloc[0]["model"])

    print(f"\nchampion model: {champion}")

    forecasts = _forecast_with(panel, champion)
    if not forecasts.empty:
        forecasts.to_csv(OUT / "workforce_forecast.csv", index=False)
        print(f"forecast rows: {len(forecasts)}")
        print("\n2030 forecast, 8 largest series:")
        y = forecasts[forecasts["year"] == 2030].nlargest(8, "forecast")
        print(y[["country_code", "profession_code", "forecast",
                 "baseline_forecast", "change_vs_2024_pct"]]
              .round(1).to_string(index=False))

    (OUT / "model_selection.json").write_text(json.dumps({
        "champion": champion,
        "comparison": comparison.to_dict(orient="records"),
        "folds": folds,
        "n_structural_breaks": int(len(breaks)),
    }, indent=2), encoding="utf-8")


def _forecast_with(panel: pd.DataFrame, champion: str) -> pd.DataFrame:
    from ml.forecasting import NaiveForecaster, all_models as _all

    model = NaiveForecaster()
    for candidate in _all():
        if candidate.name == champion:
            model = candidate
            break
    predicted = model.fit_predict(panel, TARGET_HORIZONS)
    if not predicted:
        return pd.DataFrame()
    baseline = NaiveForecaster().fit_predict(panel, TARGET_HORIZONS)

    latest = panel.sort_values("year").groupby(
        ["country_code", "profession_code"]
    )["headcount"].last()

    rows = []
    for (country, profession, year), value in predicted.items():
        key = (country, profession)
        rows.append({
            "country_code": country,
            "profession_code": profession,
            "year": year,
            "forecast": value,
            "baseline_forecast": baseline.get((country, profession, year)),
            "latest_observed_year": int(panel[
                (panel["country_code"] == country)
                & (panel["profession_code"] == profession)
            ]["year"].max()),
            "latest_observed": float(latest.get(key, float("nan"))),
        })
    frame = pd.DataFrame(rows)
    frame["change_vs_latest_pct"] = (
        (frame["forecast"] - frame["latest_observed"])
        / frame["latest_observed"].replace(0, pd.NA) * 100
    ).astype(float)
    frame = frame.rename(columns={"change_vs_latest_pct":
                                  "change_vs_2024_pct"})
    return frame.sort_values(
        ["country_code", "profession_code", "year"]
    ).reset_index(drop=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--quick", action="store_true")
    main(parser.parse_args().quick)