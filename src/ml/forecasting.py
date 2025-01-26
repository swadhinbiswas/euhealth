"""Forecasting the health workforce: multiple model families, one task.

Design decisions that matter
----------------------------
**Two tiers of model.** ARIMA and Prophet are *local*: each is fitted
independently per country-profession series and cannot borrow strength from
other series. XGBoost, LightGBM and Random Forest are *global*: one model is
fitted across the whole panel, so a short series (Cyprus reports 2 points)
inherits the structure learned from the long ones. Both tiers are compared on
the same evaluation folds because a local model on a 2-point series is
meaningless and would otherwise win or lose by luck.

**Walk-forward validation only.** A random split leaks future information into
training and produces optimistic accuracy that does not survive contact with a
real forecast. Every score here comes from training on the past and predicting
a strictly later block.

**A naive baseline is always included.** A model that cannot beat "next year
equals this year" has not earned its complexity, and without that reference the
comparison is uninterpretable.

**Structural breaks are reported, not smoothed.** Some series contain a level
shift caused by a reporting-method change rather than a real workforce event.
Forecasting across one inherits the discontinuity; the affected series are
identified and listed rather than hidden.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

import numpy as np
import pandas as pd

RANDOM_SEED = 42

#: A local model needs enough history to be identifiable. Below this, only the
#: global models and the naive baseline are used.
MIN_SERIES_LENGTH = 8

TARGET_HORIZONS = [2027, 2028, 2029, 2030, 2035]


# --- metrics -----------------------------------------------------------------

def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.mean(np.abs(actual - predicted)))


def rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    return float(np.sqrt(np.mean((actual - predicted) ** 2)))


def mape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Mean absolute percentage error, in percent.

    Guarded against division by zero: headcounts can legitimately be reported
    as zero for a small series, and an unguarded MAPE returns infinity, which
    then poisons the model comparison table.
    """
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    denom = np.where(np.abs(actual) < 1e-9, np.nan, np.abs(actual))
    return float(np.nanmean(np.abs(actual - predicted) / denom) * 100)


def smape(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Symmetric MAPE, in percent. More stable than MAPE near zero."""
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    denom = (np.abs(actual) + np.abs(predicted)) / 2
    denom = np.where(denom < 1e-9, np.nan, denom)
    return float(np.nanmean(np.abs(actual - predicted) / denom) * 100)


METRICS = {"mae": mae, "rmse": rmse, "mape": mape, "smape": smape}


def score(actual: pd.Series, predicted: pd.Series) -> dict[str, float]:
    """All four metrics for one aligned actual/predicted pair."""
    a = actual.to_numpy(dtype=float)
    p = predicted.to_numpy(dtype=float)
    return {name: fn(a, p) for name, fn in METRICS.items()}


# --- models ------------------------------------------------------------------

class Forecaster(Protocol):
    name: str

    def fit_predict(
        self, train: pd.DataFrame, future_years: list[int]
    ) -> dict[tuple, float]:
        """Fit on ``train`` and predict headcount per (country, profession, year)."""
        ...


@dataclass
class NaiveForecaster:
    """Last observed value, carried forward.

    The benchmark every other model has to beat. Also the only defensible
    choice for a series with too little history for anything else.
    """

    name: str = "naive"
    use_drift: bool = False

    def fit_predict(self, train, future_years):
        out = {}
        for (country, profession), group in train.groupby(
            ["country_code", "profession_code"]
        ):
            ordered = group.sort_values("year")
            last = float(ordered["headcount"].iloc[-1])
            if self.use_drift and len(ordered) >= 2:
                span = int(
                    ordered["year"].iloc[-1] - ordered["year"].iloc[0]
                )
                if span > 0:
                    drift = (
                        last - float(ordered["headcount"].iloc[0])
                    ) / span
                    last_year = int(ordered["year"].iloc[-1])
                    for year in future_years:
                        out[(country, profession, year)] = max(
                            0.0, last + drift * (year - last_year)
                        )
                    continue
            for year in future_years:
                out[(country, profession, year)] = max(0.0, last)
        return out


@dataclass
class DriftForecaster:
    """Linear trend through the series, clipped at zero."""

    name: str = "drift"

    def fit_predict(self, train, future_years):
        out = {}
        for (country, profession), group in train.groupby(
            ["country_code", "profession_code"]
        ):
            ordered = group.sort_values("year")
            x = ordered["year"].to_numpy(dtype=float)
            y = ordered["headcount"].to_numpy(dtype=float)
            if len(x) < 2:
                value = float(y[-1])
                for year in future_years:
                    out[(country, profession, year)] = max(0.0, value)
                continue
            slope, intercept = np.polyfit(x, y, 1)
            for year in future_years:
                out[(country, profession, year)] = max(
                    0.0, slope * year + intercept
                )
        return out


@dataclass
class ArimaForecaster:
    """ARIMA per series, order chosen by AIC over a small grid.

    A fallback counter is exposed rather than silently degrading to naive. A
    model that quietly returns the naive answer for every series is not a
    model, and reporting its accuracy as if it were would be dishonest.
    """

    name: str = "arima"
    max_order: int = 3

    def __post_init__(self):
        self.n_fitted = 0
        self.n_fallback = 0

    def _dense(self, ordered: pd.DataFrame):
        """Contiguous year-indexed series, gaps left as NaN."""
        years = ordered["year"].to_numpy(dtype=int)
        index = pd.Index(range(int(years[0]), int(years[-1]) + 1))
        return pd.Series(
            ordered["headcount"].to_numpy(dtype=float), index=index
        )

    def fit_predict(self, train, future_years):
        from statsmodels.tsa.arima.model import ARIMA

        out = {}
        for (country, profession), group in train.groupby(
            ["country_code", "profession_code"]
        ):
            ordered = group.sort_values("year")
            if len(ordered) < MIN_SERIES_LENGTH:
                self.n_fallback += 1
                out.update(
                    _naive_carry(ordered, country, profession, future_years)
                )
                continue
            try:
                dense = self._dense(ordered)
                values = dense.interpolate().ffill().bfill().to_numpy()
                if not np.isfinite(values).all() or len(values) < 4:
                    raise ValueError("degenerate series")

                best, best_aic = None, np.inf
                for p in range(self.max_order + 1):
                    for q in range(self.max_order + 1):
                        if p == 0 and q == 0:
                            continue
                        try:
                            candidate = ARIMA(
                                values, order=(p, 1, q),
                                enforce_stationarity=False,
                                enforce_invertibility=False,
                            ).fit()
                            if np.isfinite(candidate.aic) and \
                                    candidate.aic < best_aic:
                                best, best_aic = candidate, candidate.aic
                        except Exception:
                            continue
                if best is None:
                    raise ValueError("no ARIMA order converged")

                last_year = int(ordered["year"].iloc[-1])
                steps = max(future_years) - last_year
                forecast = np.asarray(best.forecast(steps=steps), dtype=float)
                for offset, year in enumerate(
                    range(last_year + 1, max(future_years) + 1)
                ):
                    out[(country, profession, year)] = max(
                        0.0, float(forecast[offset])
                    )
                self.n_fitted += 1
            except Exception:
                self.n_fallback += 1
                out.update(
                    _naive_carry(ordered, country, profession, future_years)
                )
        return out


def _naive_carry(ordered, country, profession, future_years) -> dict:
    last = float(ordered.sort_values("year")["headcount"].iloc[-1])
    return {(country, profession, year): max(0.0, last) for year in future_years}


@dataclass
class ProphetForecaster:
    """Prophet per series: trend plus changepoints, no seasonality.

    Eurostat workforce data is annual, so there is no within-year seasonality to
    learn and an explicit yearly seasonal component would be overfitting.
    """

    name: str = "prophet"

    def __post_init__(self):
        self.n_fitted = 0
        self.n_fallback = 0

    def fit_predict(self, train, future_years):
        out = {}
        for (country, profession), group in train.groupby(
            ["country_code", "profession_code"]
        ):
            ordered = group.sort_values("year")
            if len(ordered) < MIN_SERIES_LENGTH:
                self.n_fallback += 1
                out.update(
                    _naive_carry(ordered, country, profession, future_years)
                )
                continue
            try:
                from prophet import Prophet

                df = pd.DataFrame({
                    "ds": pd.to_datetime(ordered["year"].astype(str) + "-12-31"),
                    "y": ordered["headcount"].to_numpy(dtype=float),
                })
                # Eurostat workforce data is annual, so there is no within-year
                # seasonality to learn. Seasonality is disabled by switching
                # the components off; seasonality_mode=None is no longer
                # accepted by Prophet >= 1.5.
                model = Prophet(
                    yearly_seasonality=False,
                    weekly_seasonality=False,
                    daily_seasonality=False,
                    interval_width=0.80,
                )
                model.fit(df)
                horizon = pd.DataFrame({
                    "ds": pd.to_datetime(
                        [f"{y}-12-31" for y in future_years]
                    )
                })
                pred = model.predict(horizon)
                for year, value in zip(future_years, pred["yhat"]):
                    out[(country, profession, year)] = max(0.0, float(value))
                self.n_fitted += 1
            except Exception:
                self.n_fallback += 1
                out.update(
                    _naive_carry(ordered, country, profession, future_years)
                )
        return out


# --- global panel models ------------------------------------------------------

def build_features(panel: pd.DataFrame) -> pd.DataFrame:
    """Lag and trend features for the global models.

    Built on a complete year x series grid so lags line up even when a series
    has missing years; gaps are left NaN and the tree models handle them
    natively rather than being filled with a fabricated value.
    """
    frame = panel[["country_code", "profession_code", "year",
                   "headcount"]].copy()
    frame["series"] = frame["country_code"] + "_" + frame["profession_code"]
    years = range(int(frame["year"].min()), int(frame["year"].max()) + 1)
    grid = frame.pivot_table(
        index="series", columns="year", values="headcount"
    ).reindex(columns=years)

    def to_long(df: pd.DataFrame, name: str) -> pd.DataFrame:
        """Series x year matrix -> long frame with (series, year, name)."""
        long = (
            df.rename_axis(index="series", columns="year")
            .stack(future_stack=True)
            .rename(name)
            .reset_index()
        )
        long.columns = ["series", "year", name]
        long["year"] = long["year"].astype(int)
        return long

    out = to_long(grid, "headcount")
    # axis=1 shifts across *time*. The pandas default (axis=0) shifts between
    # countries, silently producing a "lag" that is another country's value.
    for lag in (1, 2, 3):
        out = out.merge(
            to_long(grid.shift(lag, axis=1), f"lag_{lag}"),
            on=["series", "year"], how="left",
        )
    out = out.merge(
        to_long(
            grid.rolling(3, min_periods=1).mean().shift(1, axis=1),
            "rolling_mean_3",
        ),
        on=["series", "year"], how="left",
    )
    out = out.merge(
        to_long(grid.pct_change(axis=1), "yoy_growth"),
        on=["series", "year"], how="left",
    )
    out = out.merge(
        to_long((grid - grid.shift(5, axis=1)) / 5.0, "trend_5y"),
        on=["series", "year"], how="left",
    )

    meta = frame.drop_duplicates("series")[
        ["series", "country_code", "profession_code"]
    ]
    out = out.merge(meta, on="series", how="left")
    out["year_index"] = out["year"] - int(out["year"].min())
    return out


GLOBAL_FEATURES = [
    "year_index", "lag_1", "lag_2", "lag_3",
    "rolling_mean_3", "yoy_growth", "trend_5y",
    "country_code", "profession_code",
]


@dataclass
class TreePanelForecaster:
    """One global model across every series, with lag features.

    ``kind`` selects the implementation so all three tree families share one
    feature pipeline and are compared on identical inputs.
    """

    kind: str = "lightgbm"
    name: str = ""

    def __post_init__(self):
        if not self.name:
            self.name = self.kind
        # Stable integer encodings shared by fit and predict. Tree models here
        # take a plain numeric matrix, not pandas categoricals.
        self._country_codes: dict[str, int] = {}
        self._profession_codes: dict[str, int] = {}

    def _encode(self, frame: pd.DataFrame, fit: bool) -> pd.DataFrame:
        out = frame.copy()
        for col, store in (
            ("country_code", self._country_codes),
            ("profession_code", self._profession_codes),
        ):
            if fit:
                store.clear()
                for i, value in enumerate(sorted(frame[col].dropna().unique())):
                    store[str(value)] = i
            out[col] = out[col].map(store)
        return out.fillna({c: -1 for c in ("country_code", "profession_code")})

    def _make(self):
        if self.kind == "lightgbm":
            from lightgbm import LGBMRegressor

            return LGBMRegressor(
                n_estimators=400, learning_rate=0.05, num_leaves=15,
                min_child_samples=10, subsample=0.8, subsample_freq=1,
                colsample_bytree=0.8, random_state=RANDOM_SEED, verbose=-1,
            )
        if self.kind == "xgboost":
            from xgboost import XGBRegressor

            return XGBRegressor(
                n_estimators=400, learning_rate=0.05, max_depth=4,
                min_child_weight=5, subsample=0.8, colsample_bytree=0.8,
                random_state=RANDOM_SEED, tree_method="hist",
                objective="reg:squarederror",
            )
        from sklearn.ensemble import RandomForestRegressor

        return RandomForestRegressor(
            n_estimators=300, max_depth=8, min_samples_leaf=3,
            random_state=RANDOM_SEED, n_jobs=-1,
        )

    def _fit_frame(self, features: pd.DataFrame, target: pd.Series):
        usable = features.notna().all(axis=1)
        if usable.sum() < 20:
            return None, None
        model = self._make()
        model.fit(features[usable], target[usable])
        return model, features.columns.tolist()

    def fit_predict(self, train, future_years):
        panel = build_features(train)
        target = panel["headcount"]
        features = self._encode(panel, fit=True)[GLOBAL_FEATURES]
        model, _ = self._fit_frame(features, target)
        if model is None:
            return NaiveForecaster().fit_predict(train, future_years)

        base_year = int(panel["year"].min())
        observed = panel[panel["headcount"].notna()].set_index(
            ["series", "year"])["headcount"]
        series_meta = panel.drop_duplicates("series").set_index("series")[
            ["country_code", "profession_code"]
        ]
        # Recursive state per series: seeded with observed values, then updated
        # with this model's own predictions as the horizon advances. Without
        # this, lag features for year+2 are unobserved and get filled with
        # zero, which is why a tree model scores catastrophically worse than a
        # linear trend.
        state: dict = {}
        for (series, year), value in observed.items():
            state.setdefault(str(series), {})[int(year)] = float(value)

        out = {}
        for year in sorted(future_years):
            rows, keys, owners = [], [], []
            for series, meta in series_meta.iterrows():
                history = state.get(series) or {}
                if not history or max(history) >= year:
                    continue
                row = {"year_index": year - base_year}
                for lag in (1, 2, 3):
                    row[f"lag_{lag}"] = history.get(year - lag, np.nan)
                recent = [history[y] for y in sorted(history)
                          if year - 3 <= y < year]
                row["rolling_mean_3"] = (
                    float(np.mean(recent)) if recent else np.nan
                )
                previous = history.get(year - 1, np.nan)
                row["yoy_growth"] = (
                    previous / history[year - 2] - 1
                    if year - 2 in history and history[year - 2] != 0
                    else np.nan
                )
                row["trend_5y"] = (
                    (previous - history[year - 5]) / 5.0
                    if year - 5 in history else np.nan
                )
                row["country_code"] = meta["country_code"]
                row["profession_code"] = meta["profession_code"]
                rows.append(row)
                keys.append((meta["country_code"], meta["profession_code"],
                             year))
                owners.append(series)
            if not rows:
                continue
            block = pd.DataFrame(rows).reindex(columns=GLOBAL_FEATURES)
            block = self._encode(block, fit=False)
            block = block.fillna(
                {c: 0.0 for c in block.columns if block[c].dtype.kind == "f"}
            )
            for key, value, series in zip(keys, model.predict(block), owners):
                value = max(0.0, float(value))
                out[key] = value
                state.setdefault(series, {})[year] = value
        return out

    def feature_importance(self, panel: pd.DataFrame) -> pd.Series:
        """Gain-based importance, normalised to sum to 1."""
        features_all = build_features(panel)
        target = features_all["headcount"]
        features = self._encode(features_all, fit=True)[GLOBAL_FEATURES]
        model, columns = self._fit_frame(features, target)
        if model is None:
            return pd.Series(dtype=float)
        if hasattr(model, "feature_importances_"):
            values = np.asarray(model.feature_importances_, dtype=float)
        else:  # pragma: no cover - all three expose the attribute
            values = np.zeros(len(columns))
        total = values.sum()
        if total <= 0:
            return pd.Series(values, index=columns)
        return pd.Series(values / total, index=columns).sort_values(
            ascending=False
        )


def all_models() -> list:
    """Every model family, in increasing order of expected sophistication."""
    return [
        NaiveForecaster(),
        DriftForecaster(),
        ArimaForecaster(),
        ProphetForecaster(),
        TreePanelForecaster("randomforest"),
        TreePanelForecaster("lightgbm"),
        TreePanelForecaster("xgboost"),
    ]


# --- validation ---------------------------------------------------------------

@dataclass
class FoldResult:
    model: str
    train_through: int
    rows: int = 0
    scores: dict = field(default_factory=dict)


def walk_forward_folds(
    panel: pd.DataFrame, min_train_years: int = 10
) -> list[tuple[int, int, int]]:
    """Expanding-window folds as (train_through, test_from, test_to).

    Training always precedes evaluation. A random split is never used: it
    leaks the future into the fit and reports accuracy that a live forecast
    will not reproduce.
    """
    years = sorted(panel["year"].unique())
    if len(years) <= min_train_years:
        return []
    folds = []
    step = max(1, len(years) // 6)
    for test_from in range(min_train_years, len(years), step):
        train_through = years[test_from - 1]
        test_to_idx = min(test_from + step - 1, len(years) - 1)
        folds.append((int(train_through), int(years[test_from]),
                      int(years[test_to_idx])))
    return folds


def evaluate_model(
    model, panel: pd.DataFrame, folds: list[tuple[int, int, int]]
) -> tuple[dict[str, float], pd.DataFrame]:
    """Aggregate metrics across every fold on the common evaluation set."""
    actuals: list[pd.Series] = []
    predictions: list[pd.Series] = []
    for train_through, test_from, test_to in folds:
        train = panel[panel["year"] <= train_through]
        test = panel[
            (panel["year"] >= test_from) & (panel["year"] <= test_to)
        ]
        if train.empty or test.empty:
            continue
        future = list(range(test_from, test_to + 1))
        predicted = model.fit_predict(train, future)
        if not predicted:
            continue
        keys, values = [], []
        for key, value in predicted.items():
            match = test[
                (test["country_code"] == key[0])
                & (test["profession_code"] == key[1])
                & (test["year"] == key[2])
            ]
            if not match.empty:
                keys.append(key)
                values.append(value)
        if not keys:
            continue
        actuals.append(pd.Series(
            [float(test[
                (test["country_code"] == k[0])
                & (test["profession_code"] == k[1])
                & (test["year"] == k[2])
            ]["headcount"].iloc[0]) for k in keys],
            index=keys,
        ))
        predictions.append(pd.Series(values, index=keys))

    if not actuals:
        return {}, pd.DataFrame()

    actual_all = pd.concat(actuals)
    pred_all = pd.concat(predictions)
    joined = pd.DataFrame({
        "actual": actual_all, "predicted": pred_all
    }).dropna()
    if joined.empty:
        return {}, pd.DataFrame()
    return score(joined["actual"], joined["predicted"]), joined.reset_index()


def detect_structural_breaks(panel: pd.DataFrame, threshold: float = 0.15
                             ) -> pd.DataFrame:
    """Series with a one-year level shift larger than ``threshold``.

    These are reporting-method changes, not workforce events. Forecasting
    across one carries the discontinuity into every future year, so they are
    listed for the analyst rather than silently smoothed away.
    """
    rows = []
    for (country, profession), group in panel.groupby(
        ["country_code", "profession_code"]
    ):
        ordered = group.sort_values("year")
        if len(ordered) < 3:
            continue
        values = ordered["headcount"].to_numpy(dtype=float)
        years = ordered["year"].to_numpy(dtype=int)
        for i in range(2, len(values)):
            past = values[max(0, i - 3):i].mean()
            if past <= 0:
                continue
            change = abs(values[i] - past) / past
            if change >= threshold:
                rows.append({
                    "country_code": country,
                    "profession_code": profession,
                    "break_year": int(years[i]),
                    "magnitude_pct": round(change * 100, 1),
                })
    return pd.DataFrame(rows).sort_values(
        "magnitude_pct", ascending=False
    ).reset_index(drop=True) if rows else pd.DataFrame()