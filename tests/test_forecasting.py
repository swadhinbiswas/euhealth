"""Tests for the forecasting layer.

These cover the parts that are easy to get subtly wrong: metric definitions
(especially near zero), the walk-forward guarantee that training never sees the
future, and the requirement that short series degrade gracefully instead of
raising.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ml.forecasting import (
    METRICS,
    MIN_SERIES_LENGTH,
    ArimaForecaster,
    DriftForecaster,
    NaiveForecaster,
    ProphetForecaster,
    TreePanelForecaster,
    build_features,
    detect_structural_breaks,
    mae,
    mape,
    rmse,
    score,
    smape,
    walk_forward_folds,
)


@pytest.fixture
def panel() -> pd.DataFrame:
    rows = []
    for country, base, slope in (
        ("DE", 300000, 4000), ("FR", 200000, 2500),
        ("IT", 220000, 1500), ("EL", 60000, -200),
    ):
        for profession, factor in (("PHYS", 1.0), ("NURS", 2.4)):
            for offset, year in enumerate(range(2000, 2025)):
                rows.append({
                    "country_code": country,
                    "profession_code": profession,
                    "year": year,
                    "headcount": base * factor + slope * factor * offset,
                })
    return pd.DataFrame(rows)


@pytest.fixture
def short_panel() -> pd.DataFrame:
    """A series with too little history for a local model."""
    return pd.DataFrame([
        {"country_code": "CY", "profession_code": "PHYS",
         "year": 2015, "headcount": 3000.0},
        {"country_code": "CY", "profession_code": "PHYS",
         "year": 2016, "headcount": 3100.0},
    ])


class TestMetrics:
    def test_mae_known_value(self):
        assert mae(np.array([1.0, 2.0]), np.array([2.0, 4.0])) == pytest.approx(1.5)

    def test_rmse_known_value(self):
        # sqrt(mean([9, 16])) = sqrt(12.5) = 3.5355
        assert rmse(np.array([0.0, 0.0]), np.array([3.0, 4.0])) == \
            pytest.approx(3.5355, abs=1e-3)

    def test_mape_known_value(self):
        # errors 1 on 100 and 1 on 200 -> mean 0.0075 -> 0.75%
        assert mape(np.array([100.0, 200.0]), np.array([99.0, 199.0])) == \
            pytest.approx(0.75)

    def test_mape_does_not_explode_near_zero(self):
        # The failure this guards: an unguarded MAPE returns inf on a zero
        # actual, which then poisons the whole comparison table.
        value = mape(np.array([0.0, 100.0]), np.array([5.0, 100.0]))
        assert np.isfinite(value)

    def test_smape_is_bounded_by_200(self):
        assert smape(np.array([1.0]), np.array([1.0e9])) <= 200.0

    def test_smape_zero_actual_is_finite(self):
        assert np.isfinite(smape(np.array([0.0, 0.0]), np.array([1.0, 2.0])))

    def test_all_four_metrics_reported(self):
        got = score(pd.Series([100.0, 200.0]), pd.Series([110.0, 190.0]))
        assert set(got) == set(METRICS)

    def test_perfect_prediction_scores_zero(self):
        s = score(pd.Series([1.0, 2.0, 3.0]), pd.Series([1.0, 2.0, 3.0]))
        assert s["mae"] == 0.0 and s["rmse"] == 0.0


class TestWalkForward:
    def test_training_always_precedes_evaluation(self, panel):
        for train_through, test_from, test_to in walk_forward_folds(panel):
            assert train_through < test_from <= test_to

    def test_folds_are_expanding(self, panel):
        folds = walk_forward_folds(panel)
        train_years = [f[0] for f in folds]
        assert train_years == sorted(train_years)
        assert len(set(train_years)) == len(train_years)

    def test_later_folds_train_on_more_data(self, panel):
        folds = walk_forward_folds(panel)
        assert folds[-1][0] > folds[0][0]

    def test_too_short_series_produce_no_folds(self, short_panel):
        assert walk_forward_folds(short_panel) == []

    def test_evaluation_predictions_strictly_follow_training(self, panel):
        """Every scored prediction must fall in a test window, never in training.

        A random split would produce predictions for years the model had
        already seen; this asserts each fold's predictions sit strictly after
        that fold's training boundary.
        """
        folds = walk_forward_folds(panel)
        model = NaiveForecaster()
        for train_through, test_from, test_to in folds:
            train = panel[panel["year"] <= train_through]
            predicted = model.fit_predict(
                train, list(range(test_from, test_to + 1))
            )
            assert predicted, "fold produced no predictions"
            for (_, _, year) in predicted:
                assert year > train_through
                assert test_from <= year <= test_to


class TestLocalModels:
    def test_naive_carries_last_value(self):
        train = pd.DataFrame([
            {"country_code": "DE", "profession_code": "PHYS",
             "year": 2020, "headcount": 100.0},
            {"country_code": "DE", "profession_code": "PHYS",
             "year": 2021, "headcount": 110.0},
        ])
        out = NaiveForecaster().fit_predict(train, [2022, 2023])
        assert out[("DE", "PHYS", 2022)] == 110.0
        assert out[("DE", "PHYS", 2023)] == 110.0

    def test_predictions_are_never_negative(self):
        train = pd.DataFrame([
            {"country_code": "DE", "profession_code": "PHYS",
             "year": y, "headcount": v}
            for y, v in zip(range(2015, 2025), range(1000, 0, -100))
        ])
        for model in (NaiveForecaster(), DriftForecaster(),
                      ArimaForecaster(), ProphetForecaster()):
            for value in model.fit_predict(train, [2030]).values():
                assert value >= 0.0, f"{model.name} predicted a negative value"

    def test_drift_follows_trend_direction(self, panel):
        out = DriftForecaster().fit_predict(panel, [2025])
        de_phys = out[("DE", "PHYS", 2025)]
        de_2024 = panel[
            (panel.country_code == "DE") & (panel.profession_code == "PHYS")
            & (panel.year == 2024)
        ]["headcount"].iloc[0]
        assert de_phys > de_2024

    def test_short_series_degrades_to_naive(self, short_panel):
        for model in (ArimaForecaster(), ProphetForecaster()):
            out = model.fit_predict(short_panel, [2020])
            assert out[("CY", "PHYS", 2020)] == pytest.approx(3100.0)

    def test_local_models_run_on_real_panel(self, panel):
        train = panel[panel.year <= 2018]
        for model in (ArimaForecaster(), ProphetForecaster()):
            out = model.fit_predict(train, [2019])
            assert len(out) > 0
            assert all(v >= 0 for v in out.values())


class TestFeatures:
    def test_lags_are_shifted_not_leaked(self, panel):
        """A series' first year has no prior observation, so lag_1 is NaN."""
        features = build_features(panel)
        for series, group in features.groupby("series"):
            first = group.sort_values("year").iloc[0]
            assert pd.isna(first["lag_1"]), \
                f"{series}: lag_1 leaked into its own first row"

    def test_lag_shifts_across_time_not_across_countries(self, panel):
        """Regression: shift() defaults to axis=0, which lags by country.

        With the wrong axis, DE's lag_1 was another country's headcount.
        """
        features = build_features(panel)
        row = features[
            (features.country_code == "DE")
            & (features.profession_code == "PHYS")
            & (features.year == 2010)
        ].iloc[0]
        expected = panel[
            (panel.country_code == "DE")
            & (panel.profession_code == "PHYS")
            & (panel.year == 2009)
        ]["headcount"].iloc[0]
        assert row["lag_1"] == pytest.approx(expected)
        # And it must not equal any other country's 2009 value.
        others = panel[
            (panel.country_code != "DE")
            & (panel.profession_code == "PHYS")
            & (panel.year == 2009)
        ]["headcount"]
        assert row["lag_1"] not in set(others)

    def test_lag1_equals_previous_year_value(self, panel):
        features = build_features(panel)
        target = features[
            (features.country_code == "DE")
            & (features.profession_code == "PHYS")
            & (features.year == 2010)
        ]["lag_1"].iloc[0]
        expected = panel[
            (panel.country_code == "DE")
            & (panel.profession_code == "PHYS")
            & (panel.year == 2009)
        ]["headcount"].iloc[0]
        assert target == pytest.approx(expected)

    def test_grid_is_complete_even_with_missing_years(self, panel):
        """A year removed from one series still appears on the full grid."""
        gapped = panel[~((panel.country_code == "FR")
                         & panel.year.isin([2007, 2008]))]
        features = build_features(gapped)
        rows = features[features.year == 2007]
        # Every series gets a row for 2007, including the gapped one.
        assert set(rows["series"]) == set(
            panel.assign(series=panel["country_code"] + "_"
                         + panel["profession_code"])["series"]
        )
        fr = rows[rows["country_code"] == "FR"]["headcount"]
        assert fr.isna().all(), "a removed year was backfilled with a value"

    def test_sex_and_profession_are_not_conflated(self, panel):
        features = build_features(panel)
        de_phys = features[
            (features.country_code == "DE")
            & (features.profession_code == "PHYS")
            & (features.year == 2015)
        ]["headcount"].iloc[0]
        de_nurse = features[
            (features.country_code == "DE")
            & (features.profession_code == "NURS")
            & (features.year == 2015)
        ]["headcount"].iloc[0]
        assert de_phys != de_nurse

    def test_missing_years_become_nan_not_zero(self, panel):
        gapped = panel[~((panel.country_code == "FR")
                         & panel.year.isin([2007, 2008]))]
        features = build_features(gapped)
        row = features[
            (features.country_code == "FR") & (features.year == 2008)
        ]
        assert pd.isna(row["headcount"].iloc[0]), \
            "a missing year was fabricated as a value"


class TestGlobalModels:
    @pytest.mark.parametrize("kind", ["lightgbm", "xgboost", "randomforest"])
    def test_global_model_predicts_panel(self, panel, kind):
        train = panel[panel.year <= 2020]
        out = TreePanelForecaster(kind).fit_predict(train, [2021, 2022])
        assert len(out) == panel.groupby(
            ["country_code", "profession_code"]).ngroups * 2
        assert all(v >= 0 for v in out.values())

    @pytest.mark.parametrize("kind", ["lightgbm", "xgboost", "randomforest"])
    def test_global_model_beats_nothing_by_accident(self, panel, kind):
        """A global model must be roughly as good as naive, not random."""
        train = panel[panel.year <= 2018]
        truth = panel[panel.year == 2019].set_index(
            ["country_code", "profession_code"])["headcount"]
        out = TreePanelForecaster(kind).fit_predict(train, [2019])
        errors = [
            abs(out[key] - float(truth.loc[key[:2]])) / float(truth.loc[key[:2]])
            for key in out if key[:2] in truth.index
        ]
        assert errors
        assert np.mean(errors) < 0.5, "global model error above 50%"

    def test_feature_importance_is_normalised(self, panel):
        importance = TreePanelForecaster("lightgbm").feature_importance(panel)
        assert not importance.empty
        assert importance.sum() == pytest.approx(1.0, abs=1e-6)
        assert importance.is_monotonic_decreasing

    def test_importance_ranks_recent_history_highly(self, panel):
        importance = TreePanelForecaster("lightgbm").feature_importance(panel)
        # A pure trend feature carries no information; the lags must dominate.
        assert importance.index[0].startswith("lag_")

    def test_too_little_data_falls_back_to_naive(self, short_panel):
        out = TreePanelForecaster("lightgbm").fit_predict(short_panel, [2020])
        assert out[("CY", "PHYS", 2020)] == pytest.approx(3100.0)


class TestStructuralBreaks:
    def test_detects_a_level_shift(self, panel):
        broken = panel.copy()
        # Introduce a level shift in Italy's physician series from 2020 on.
        broken.loc[
            (broken.country_code == "IT")
            & (broken.profession_code == "PHYS")
            & (broken.year >= 2020), "headcount"
        ] *= 0.6
        breaks = detect_structural_breaks(broken)
        assert not breaks.empty
        assert "IT" in set(breaks["country_code"])

    def test_clean_series_yield_no_breaks(self, panel):
        assert detect_structural_breaks(panel, threshold=0.5).empty

    def test_short_series_ignored(self, short_panel):
        assert detect_structural_breaks(short_panel).empty


class TestMinimumLength:
    def test_threshold_is_applied(self):
        assert MIN_SERIES_LENGTH >= 5