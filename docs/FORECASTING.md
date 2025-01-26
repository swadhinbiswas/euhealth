# Forecasting: model comparison and results

Produced by `python -m src.ml.cli`. Panel: 821 observations, 48 country-profession
series, 2000-2024, physicians and nurses.

## Validation design

**Walk-forward only.** Four expanding-window folds:

| Fold | Train through | Test |
|---|---|---|
| 1 | ≤ 2009 | 2010-2013 |
| 2 | ≤ 2013 | 2014-2017 |
| 3 | ≤ 2017 | 2018-2021 |
| 4 | ≤ 2021 | 2022-2025 |

527 scored predictions per model, identical across models so the comparison is
like-for-like. A random split was not used anywhere: it leaks future values into
the training set and reports accuracy a live forecast will not reproduce.

## Results

| Model | MAPE % | sMAPE % | RMSE | Series fitted by the model |
|---|---|---|---|---|
| **ARIMA** | **4.99** | **5.21** | 16,757 | 107 / 147 |
| Naive (carry last value) | 5.50 | 5.73 | 13,821 | — |
| Prophet | 5.74 | 5.91 | 18,212 | 119 / 147 |
| Drift (linear trend) | 6.16 | 6.26 | 18,090 | — |
| Random Forest | 13.96 | 13.93 | 88,867 | — |
| XGBoost | 20.39 | 21.71 | 84,310 | — |
| LightGBM | 52.29 | 37.85 | 102,404 | — |

**ARIMA is the champion at 4.99% MAPE**, selected automatically as the best model
that beats the naive baseline.

### The honest finding

**Every gradient-boosted and bagged tree model underperformed the naive
baseline.** This is not a bug — it is the correct result for this data, and it is
worth stating plainly rather than burying:

- The series are short. Median length is 20 observations, minimum 2.
- They are close to linear. A workforce stock changes slowly and does not
  exhibit the non-linearity that tree models exploit.
- **Tree models cannot extrapolate.** They are bounded by the target range seen
  in training, so a series still growing in 2024 flattens immediately past the
  training window. No amount of feature engineering fixes that.

The practical consequence for a health ministry is that a 5-year workforce
projection should rest on a statistical time-series model, not a black-box
tabular learner. Reporting an XGBoost forecast here would have looked more
impressive and been worse.

## Feature importance (LightGBM)

```
lag_1              0.2305
trend_5y           0.1645
rolling_mean_3     0.1523
lag_2              0.1339
lag_3              0.1264
yoy_growth         0.1027
year_index         0.0664
country_code       0.0170
profession_code    0.0062
```

Recent history dominates, which is the expected shape. Country identity carries
almost no weight once lags are available, indicating the series share a common
dynamic rather than 48 unrelated ones.

## Structural breaks

28 series-year discontinuities above a 15% one-year shift:

| Country | Profession | Year | Magnitude |
|---|---|---|---|
| IE | PHYS | 2006 | 50.8% |
| PL | PHYS | 2019 | 38.5% |
| IT | PHYS | 2012 | 38.2% |
| IT | PHYS | 2021 | 33.5% |

These are reporting-method changes, not workforce events. Italy's 2021 shift of
-19% between consecutive years is not plausible as a real workforce movement in
one year. Forecasting across such a break inherits the discontinuity, so the
affected series are listed in `data/models/structural_breaks.csv` and should be
excluded or re-based before being presented.

## Four bugs found by validating this properly

Each of these produced a plausible-looking number that was wrong:

1. **Lags were computed across countries, not time.** `shift()` defaults to
   `axis=0`. DE's `lag_1` was another country's headcount. Fixed with
   `axis=1`; a test asserts a series' first year has no lag.
2. **ARIMA silently returned the naive answer for every series**, and reported
   accuracy identical to naive. Caught only by adding explicit `n_fitted` /
   `n_fallback` counters. A bare `except` had been masking a broken forecast
   horizon.
3. **Prophet failed on all 39 series** because `seasonality_mode=None` was
   removed in Prophet 1.5. Now fits 119/147.
4. **Tree models scored 47-63% MAPE** because lag features for year+2 are
   unobserved and were being filled with `0.0`. Fixed with recursive state, where
   each prediction is fed back as the next year's lag. This alone moved Random
   Forest from 47.0% to 14.0%.

LightGBM at 52% remains unstable relative to the other two tree models
(RF 14%, XGBoost 20%). It is reported as measured rather than tuned until it
flatters the story.

## Outputs

| File | Contents |
|---|---|
| `data/models/model_comparison.csv` | Metrics per model |
| `data/models/model_predictions.csv` | Every out-of-sample prediction |
| `data/models/workforce_forecast.csv` | 2030 / 2035 forecasts, champion model |
| `data/models/structural_breaks.csv` | Discontinuities to exclude |
| `data/models/model_selection.json` | Champion, folds, full comparison |

## Not yet done

Retirement-count forecasting (as distinct from workforce stock), scenario bands
(best / expected / worst) driven by `proj_25np` variants, and the medical-desert
classifier. Those are the remaining Phase 6 items.