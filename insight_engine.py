from __future__ import annotations

from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy import stats
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder


def build_explanations(insight: Dict[str, Any], why: List[str]) -> Dict[str, Any]:
    enriched = dict(insight)
    enriched['why_this_matters'] = why
    return enriched


def confidence_label(score: float) -> str:
    if score >= 80:
        return 'high'
    if score >= 60:
        return 'medium'
    return 'low'


def confidence_score(
    sample_size: int,
    effect_size: float = 0.0,
    p_value: float | None = None,
    stability: float | None = None,
) -> float:
    sample_component = min(max(sample_size, 0) / 80, 1.0) * 35
    effect_component = min(abs(effect_size), 2.0) / 2.0 * 35
    significance_component = 0.0

    if p_value is not None and np.isfinite(p_value):
        significance_component = max(0.0, 1 - (min(float(p_value), 0.2) / 0.2)) * 30
    elif stability is not None:
        significance_component = min(max(float(stability), 0.0), 1.0) * 20

    return round(min(99.0, sample_component + effect_component + significance_component), 1)


def priority_score(score: float, confidence: float) -> float:
    return round(float(np.log1p(abs(score))) * max(confidence / 20, 1), 3)


def enrich_with_statistics(
    insight: Dict[str, Any],
    why: List[str],
    *,
    sample_size: int,
    effect_size: float = 0.0,
    p_value: float | None = None,
    stability: float | None = None,
) -> Dict[str, Any]:
    enriched = build_explanations(insight, why)
    confidence = confidence_score(sample_size, effect_size=effect_size, p_value=p_value, stability=stability)
    enriched['sample_size'] = int(sample_size)
    enriched['effect_size'] = round(float(effect_size), 4) if np.isfinite(effect_size) else None
    enriched['p_value'] = round(float(p_value), 6) if p_value is not None and np.isfinite(p_value) else None
    enriched['confidence_score'] = confidence
    enriched['confidence_label'] = confidence_label(confidence)
    enriched['priority_score'] = priority_score(float(insight.get('score', 0)), confidence)
    return enriched


def anomaly_insights(df: pd.DataFrame, numeric_columns: List[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    for column in numeric_columns[:8]:
        series = pd.to_numeric(df[column], errors='coerce').dropna().astype(float)
        if len(series) < 12:
            continue
        median = float(series.median())
        mad = float(stats.median_abs_deviation(series, scale='normal'))
        if not np.isfinite(mad) or mad == 0:
            continue
        robust_z = 0.6745 * (series - median) / mad
        outliers = series[robust_z.abs() >= 3.5].sort_values(key=lambda values: values.abs(), ascending=False)
        if outliers.empty:
            continue

        top_value = float(outliers.iloc[0])
        effect_size = float(min(3.0, robust_z.abs().max()) / 3.5)
        findings.append(enrich_with_statistics({
            'kind': 'anomaly',
            'title': f'{column} shows statistically unusual values',
            'detail': f'The strongest robust outlier observed is {top_value:,.2f}, far outside the typical median-centered range.',
            'score': round(float(robust_z.abs().max()), 3),
            'stat': f'{len(outliers)} outliers (robust z >= 3.5)',
            'recommended_chart': {
                'title': f'{column} anomaly scan',
                'description': 'Use a box plot or histogram to inspect extreme points before trusting aggregate results.',
                'type': 'box',
                'columns': [column],
                'sample_percentage': 100,
            }
        }, [
            'Outlier detection uses a median-based robust z-score, which is less sensitive to distortion from extreme points.',
            'Extreme values can disproportionately change averages, forecasts, and driver explanations.',
        ], sample_size=len(series), effect_size=effect_size, stability=min(1.0, len(outliers) / max(len(series) * 0.1, 1))))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:2]


def _anova_effect(groups: List[np.ndarray]) -> tuple[float | None, float]:
    if len(groups) < 2:
        return None, 0.0
    valid_groups = [group for group in groups if len(group) >= 2]
    if len(valid_groups) < 2:
        return None, 0.0

    try:
        _, p_value = stats.f_oneway(*valid_groups)
    except Exception:
        p_value = None

    combined = np.concatenate(valid_groups)
    grand_mean = combined.mean()
    ss_between = sum(len(group) * float((group.mean() - grand_mean) ** 2) for group in valid_groups)
    ss_total = float(((combined - grand_mean) ** 2).sum())
    eta_squared = (ss_between / ss_total) if ss_total else 0.0
    return p_value, eta_squared


def segment_driver_insights(df: pd.DataFrame, dimensions: List[str], measures: List[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    for dimension in dimensions[:6]:
        cardinality = df[dimension].nunique(dropna=True)
        if cardinality < 2 or cardinality > 12:
            continue

        for measure in measures[:6]:
            grouped = df[[dimension, measure]].dropna()
            if len(grouped) < 20 or grouped[measure].nunique() < 2:
                continue

            summary = grouped.groupby(dimension)[measure].mean().sort_values(ascending=False)
            if len(summary) < 2:
                continue

            groups = [
                pd.to_numeric(group[measure], errors='coerce').dropna().to_numpy(dtype=float)
                for _, group in grouped.groupby(dimension)
            ]
            p_value, eta_squared = _anova_effect(groups)
            if p_value is not None and p_value > 0.1:
                continue

            top_segment = summary.index[0]
            bottom_segment = summary.index[-1]
            spread = float(summary.iloc[0] - summary.iloc[-1])
            pooled_std = float(grouped[measure].std(ddof=0) or 0)
            standardized_gap = abs(spread / pooled_std) if pooled_std else abs(spread)
            if spread == 0 or standardized_gap < 0.35:
                continue

            findings.append(enrich_with_statistics({
                'kind': 'segment_driver',
                'title': f'{dimension} strongly separates {measure}',
                'detail': f"{top_segment} leads while {bottom_segment} trails, with an average gap of {spread:,.2f}.",
                'score': round(abs(spread), 3),
                'stat': f'{top_segment} vs {bottom_segment}',
                'recommended_chart': {
                    'title': f'{measure} by {dimension}',
                    'description': 'Rank segments to see which groups are driving the measure up or down.',
                    'type': 'bar',
                    'columns': [dimension, measure],
                    'sample_percentage': 100,
                }
            }, [
                'The engine compares average metric values across segments so group size does not completely dominate the result.',
                'The signal is confidence-scored using segment sample size and between-group separation strength.',
            ], sample_size=len(grouped), effect_size=max(eta_squared, standardized_gap / 3), p_value=p_value))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:3]


def contribution_insights(df: pd.DataFrame, dimensions: List[str], measures: List[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []

    for dimension in dimensions[:6]:
        cardinality = df[dimension].nunique(dropna=True)
        if cardinality < 2 or cardinality > 12:
            continue

        for measure in measures[:6]:
            grouped = df[[dimension, measure]].dropna()
            if len(grouped) < 10:
                continue

            contribution = grouped.groupby(dimension)[measure].sum().sort_values(ascending=False)
            total = float(contribution.sum())
            if total == 0 or len(contribution) < 2:
                continue

            shares = (contribution / total * 100).sort_values(ascending=False)
            top_segment = shares.index[0]
            top_share = float(shares.iloc[0])
            concentration = float(((shares / 100) ** 2).sum())
            if top_share < 35:
                continue

            findings.append(enrich_with_statistics({
                'kind': 'contribution',
                'title': f'{top_segment} dominates contribution to {measure}',
                'detail': f'{top_segment} contributes {top_share:.2f}% of the total {measure}.',
                'score': round(top_share, 3),
                'stat': f'{top_share:.2f}% share',
                'recommended_chart': {
                    'title': f'{measure} contribution by {dimension}',
                    'description': 'Use a ranked view to understand which segments contribute the most to the total.',
                    'type': 'bar',
                    'columns': [dimension, measure],
                    'sample_percentage': 100,
                }
            }, [
                'Contribution is measured using the segment share of the total metric value.',
                'High concentration can signal dependency risk, channel imbalance, or a focused growth opportunity.',
            ], sample_size=len(grouped), effect_size=max(concentration, top_share / 100), stability=min(1.0, top_share / 60)))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:2]


def seasonality_insights(df: pd.DataFrame, datetime_columns: List[str], measures: List[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not datetime_columns or not measures:
        return findings

    for date_column in datetime_columns[:2]:
        for measure in measures[:4]:
            working = df[[date_column, measure]].dropna()
            if len(working) < 12:
                continue

            working = working.copy()
            working[date_column] = pd.to_datetime(working[date_column], errors='coerce')
            working[measure] = pd.to_numeric(working[measure], errors='coerce')
            working = working.dropna()
            if len(working) < 12:
                continue

            working['period'] = working[date_column].dt.to_period('M')
            monthly = working.groupby('period')[measure].sum().sort_index()
            if len(monthly) < 6:
                continue

            monthly_df = monthly.reset_index(name='metric')
            monthly_df['month_num'] = monthly_df['period'].dt.month
            month_profile = monthly_df.groupby('month_num')['metric'].mean().sort_values(ascending=False)
            if month_profile.nunique() < 2:
                continue

            groups = [
                monthly_df.loc[monthly_df['month_num'] == month, 'metric'].to_numpy(dtype=float)
                for month in sorted(monthly_df['month_num'].unique())
                if (monthly_df['month_num'] == month).sum() >= 2
            ]
            p_value, eta_squared = _anova_effect(groups)
            spread = float(month_profile.max() - month_profile.min())
            mean_level = abs(float(month_profile.mean())) or 1.0
            seasonal_strength = spread / mean_level
            if seasonal_strength < 0.15:
                continue

            peak = int(month_profile.index[0])
            trough = int(month_profile.index[-1])
            findings.append(enrich_with_statistics({
                'kind': 'seasonality',
                'title': f'{measure} shows a repeating month pattern',
                'detail': f'Month {peak} tends to be strongest while month {trough} is weakest based on repeated monthly totals.',
                'score': round(abs(spread), 3),
                'stat': f'month {peak} vs month {trough}',
                'recommended_chart': {
                    'title': f'{measure} over time',
                    'description': 'Inspect the time series to see whether a repeating cycle or seasonal pattern is present.',
                    'type': 'line',
                    'columns': [date_column, measure],
                    'sample_percentage': 100,
                }
            }, [
                'Monthly totals are compared across repeated month-of-year positions rather than raw row order.',
                'Seasonality only surfaces when the spread is meaningful relative to the typical monthly level.',
            ], sample_size=len(monthly), effect_size=max(eta_squared, seasonal_strength), p_value=p_value, stability=min(1.0, seasonal_strength)))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:1]


def forecast_insights(df: pd.DataFrame, datetime_columns: List[str], measures: List[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not datetime_columns or not measures:
        return findings

    for date_column in datetime_columns[:1]:
        for measure in measures[:3]:
            working = df[[date_column, measure]].dropna().copy()
            if len(working) < 12:
                continue

            working[date_column] = pd.to_datetime(working[date_column], errors='coerce')
            working[measure] = pd.to_numeric(working[measure], errors='coerce')
            working = working.dropna()
            if len(working) < 12:
                continue

            working['period'] = working[date_column].dt.to_period('M')
            monthly = working.groupby('period')[measure].sum().sort_index()
            if len(monthly) < 6:
                continue

            y = monthly.to_numpy(dtype=float)
            x = np.arange(len(y), dtype=float)
            regression = stats.linregress(x, y)
            if not np.isfinite(regression.slope):
                continue

            forecast_horizon = min(3, max(1, len(monthly) // 6))
            forecast_x = np.arange(len(y), len(y) + forecast_horizon, dtype=float)
            forecast_y = regression.intercept + regression.slope * forecast_x
            latest_value = float(y[-1])
            next_value = float(forecast_y[0])
            delta = next_value - latest_value
            baseline = abs(latest_value) or 1.0
            delta_pct = delta / baseline
            if abs(delta_pct) < 0.05 and abs(float(regression.rvalue or 0)) < 0.35:
                continue

            direction = 'grow' if delta >= 0 else 'decline'
            findings.append(enrich_with_statistics({
                'kind': 'forecast',
                'title': f'{measure} is projected to {direction} next period',
                'detail': f'A linear period forecast points to {next_value:,.2f} next, versus the latest {latest_value:,.2f}.',
                'score': round(abs(delta_pct) * 100, 3),
                'stat': f'next period {next_value:,.2f} ({delta_pct * 100:+.1f}%)',
                'recommended_chart': {
                    'title': f'{measure} forecast outlook',
                    'description': 'Use a trend view with a forecast extension to inspect the expected next-period direction.',
                    'type': 'line',
                    'columns': [date_column, measure],
                    'sample_percentage': 100,
                }
            }, [
                'The forecast is based on period-level totals and a simple linear regression, so it is interpretable and easy to challenge.',
                'This is best used as directional guidance, especially when paired with anomaly and seasonality context.',
            ], sample_size=len(monthly), effect_size=max(abs(delta_pct), abs(float(regression.rvalue or 0))), p_value=regression.pvalue))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:1]


def decomposition_insights(df: pd.DataFrame, datetime_columns: List[str], measures: List[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not datetime_columns or not measures:
        return findings

    for date_column in datetime_columns[:1]:
        for measure in measures[:3]:
            working = df[[date_column, measure]].dropna().copy()
            if len(working) < 16:
                continue

            working[date_column] = pd.to_datetime(working[date_column], errors='coerce')
            working[measure] = pd.to_numeric(working[measure], errors='coerce')
            working = working.dropna()
            if len(working) < 16:
                continue

            working['period'] = working[date_column].dt.to_period('M')
            monthly = working.groupby('period')[measure].sum().sort_index()
            if len(monthly) < 8:
                continue

            values = monthly.astype(float)
            trend = values.rolling(window=3, min_periods=2, center=True).mean().bfill().ffill()
            residual = values - trend
            seasonal_profile = residual.groupby(residual.index.month).mean()
            seasonal_range = float(seasonal_profile.max() - seasonal_profile.min()) if not seasonal_profile.empty else 0.0
            trend_change = float(trend.iloc[-1] - trend.iloc[0])
            baseline = abs(float(values.mean())) or 1.0
            if abs(trend_change) / baseline < 0.08 and seasonal_range / baseline < 0.1:
                continue

            strongest_month = int(seasonal_profile.sort_values(ascending=False).index[0]) if not seasonal_profile.empty else None
            weakest_month = int(seasonal_profile.sort_values(ascending=True).index[0]) if not seasonal_profile.empty else None
            findings.append(enrich_with_statistics({
                'kind': 'decomposition',
                'title': f'{measure} can be split into trend and recurring pattern',
                'detail': f'The smoothed trend changed by {trend_change:,.2f}, while the month-of-year seasonal swing spans {seasonal_range:,.2f}.',
                'score': round(max(abs(trend_change), abs(seasonal_range)), 3),
                'stat': f'trend delta {trend_change:,.2f} / seasonal range {seasonal_range:,.2f}',
                'recommended_chart': {
                    'title': f'{measure} trend vs seasonal pattern',
                    'description': 'Inspect a time-series view to separate structural movement from repeating calendar effects.',
                    'type': 'line',
                    'columns': [date_column, measure],
                    'sample_percentage': 100,
                }
            }, [
                'The engine smooths the time series to estimate a core trend, then attributes the remaining repeated month effect to seasonality.',
                'This helps distinguish durable momentum from calendar-driven volatility before stakeholders react to a single spike.',
                *( [f'Month {strongest_month} tends to run above baseline while month {weakest_month} tends to run below it.'] if strongest_month and weakest_month else [] ),
            ], sample_size=len(monthly), effect_size=max(abs(trend_change) / baseline, seasonal_range / baseline), stability=min(1.0, seasonal_range / baseline)))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:1]


def variance_explanation_insights(
    df: pd.DataFrame,
    datetime_columns: List[str],
    dimensions: List[str],
    measures: List[str],
) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    if not datetime_columns or not dimensions or not measures:
        return findings

    for date_column in datetime_columns[:1]:
        for measure in measures[:4]:
            working = df[[date_column, measure] + dimensions[:4]].copy()
            working[date_column] = pd.to_datetime(working[date_column], errors='coerce')
            working[measure] = pd.to_numeric(working[measure], errors='coerce')
            working = working.dropna(subset=[date_column, measure])
            if len(working) < 12:
                continue

            working['period'] = working[date_column].dt.to_period('M')
            period_totals = working.groupby('period')[measure].sum().sort_index()
            if len(period_totals) < 2:
                continue

            previous_period = period_totals.index[-2]
            latest_period = period_totals.index[-1]
            total_delta = float(period_totals.iloc[-1] - period_totals.iloc[-2])
            baseline = abs(float(period_totals.iloc[-2])) or 1.0
            if total_delta == 0:
                continue

            for dimension in dimensions[:4]:
                segment_periods = working.groupby(['period', dimension])[measure].sum().unstack(fill_value=0)
                if previous_period not in segment_periods.index or latest_period not in segment_periods.index:
                    continue
                deltas = (segment_periods.loc[latest_period] - segment_periods.loc[previous_period]).sort_values(
                    key=lambda values: values.abs(),
                    ascending=False,
                )
                if deltas.empty:
                    continue
                top_segment = deltas.index[0]
                top_delta = float(deltas.iloc[0])
                share = abs(top_delta / total_delta * 100) if total_delta else 0
                effect_size = max(share / 100, abs(total_delta) / baseline)
                if share < 20:
                    continue
                supporting_rows = int(working[(working['period'].isin([previous_period, latest_period])) & (working[dimension] == top_segment)].shape[0])
                findings.append(enrich_with_statistics({
                    'kind': 'variance_explanation',
                    'title': f'{dimension} explains the latest change in {measure}',
                    'detail': f"{top_segment} contributed {top_delta:,.2f} of the latest period change ({share:.1f}% of net movement).",
                    'score': round(abs(top_delta), 3),
                    'stat': f'{latest_period} vs {previous_period}',
                    'recommended_chart': {
                        'title': f'{measure} variance by {dimension}',
                        'description': 'Compare segment movement between recent periods to explain what changed.',
                        'type': 'bar',
                        'columns': [dimension, measure],
                        'sample_percentage': 100,
                    }
                }, [
                    'The engine compares the latest monthly total with the previous monthly total.',
                    'Segment deltas identify which group most explains the net movement rather than just which group is largest.',
                ], sample_size=supporting_rows, effect_size=effect_size, stability=min(1.0, share / 100)))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:2]


def funnel_insights(df: pd.DataFrame, measures: List[str]) -> List[Dict[str, Any]]:
    stage_order = ['visit', 'view', 'lead', 'signup', 'trial', 'cart', 'checkout', 'purchase', 'paid']
    matched = []
    for column in measures:
        name = column.lower()
        for idx, token in enumerate(stage_order):
            if token in name:
                matched.append((idx, column))
                break

    matched = sorted(matched)
    if len(matched) < 2:
        return []

    stages = [column for _, column in matched[:5]]
    totals = [(column, float(pd.to_numeric(df[column], errors='coerce').sum())) for column in stages]
    drops = []
    for (from_col, from_total), (to_col, to_total) in zip(totals, totals[1:]):
        if from_total <= 0:
            continue
        conversion = to_total / from_total * 100
        drops.append((from_col, to_col, conversion, from_total - to_total))

    if not drops:
        return []

    weakest = sorted(drops, key=lambda item: item[2])[0]
    total_volume = int(sum(max(total, 0) for _, total in totals))
    return [enrich_with_statistics({
        'kind': 'funnel',
        'title': f'Largest funnel friction appears between {weakest[0]} and {weakest[1]}',
        'detail': f'The observed conversion between these stages is {weakest[2]:.2f}%, with a drop of {weakest[3]:,.2f}.',
        'score': round(100 - weakest[2], 3),
        'stat': f'{weakest[2]:.2f}% conversion',
        'recommended_chart': {
            'title': 'Funnel stage totals',
            'description': 'Review stage totals to see where the funnel loses the most volume.',
            'type': 'bar',
            'columns': stages[:2],
            'sample_percentage': 100,
        }
    }, [
        'Funnel stages were inferred from common stage names in numeric columns.',
        'The weakest stage-to-stage conversion is usually the first place to investigate process friction.',
    ], sample_size=total_volume, effect_size=max(0.0, (100 - weakest[2]) / 100), stability=min(1.0, total_volume / 500))]


def retention_cohort_insights(
    df: pd.DataFrame,
    datetime_columns: List[str],
    identifier_columns: List[str],
) -> List[Dict[str, Any]]:
    if not datetime_columns or not identifier_columns:
        return []

    date_column = datetime_columns[0]
    id_column = identifier_columns[0]
    working = df[[date_column, id_column]].copy()
    working[date_column] = pd.to_datetime(working[date_column], errors='coerce')
    working[id_column] = working[id_column].astype(str)
    working = working.dropna(subset=[date_column, id_column])
    if working[id_column].nunique() < 10 or len(working) < 20:
        return []

    working['period'] = working[date_column].dt.to_period('M')
    first_period = working.groupby(id_column)['period'].min()
    working = working.join(first_period.rename('cohort'), on=id_column)
    cohort_sizes = working.groupby('cohort')[id_column].nunique()
    repeat_counts = working[working['period'] > working['cohort']].groupby('cohort')[id_column].nunique()
    retention = (repeat_counts / cohort_sizes * 100).dropna()
    if retention.empty:
        return []

    strongest = retention.sort_values(ascending=False).index[0]
    value = float(retention.loc[strongest])
    cohort_size = int(cohort_sizes.loc[strongest])
    return [enrich_with_statistics({
        'kind': 'retention_cohort',
        'title': f'{strongest} cohort has the strongest repeat activity',
        'detail': f'{value:.2f}% of entities in that cohort appear again in a later period.',
        'score': round(value, 3),
        'stat': f'{value:.2f}% repeat rate',
        'recommended_chart': {
            'title': f'{id_column} cohort retention',
            'description': 'Inspect repeat activity by cohort to understand retention-style behavior.',
            'type': 'heatmap',
            'columns': [date_column, id_column],
            'sample_percentage': 100,
        }
    }, [
        "Cohorts were inferred from each identifier's first observed month.",
        'Repeat activity is estimated by checking whether an entity appears again in later periods.',
    ], sample_size=cohort_size, effect_size=value / 100, stability=min(1.0, value / 100))]


def model_driver_insights(df: pd.DataFrame, dimensions: List[str], measures: List[str]) -> List[Dict[str, Any]]:
    findings: List[Dict[str, Any]] = []
    usable_dimensions = [column for column in dimensions if df[column].nunique(dropna=True) <= 15][:4]
    if not usable_dimensions or len(measures) < 2:
        return findings

    for target_measure in measures[:2]:
        peer_measures = [column for column in measures if column != target_measure][:3]
        feature_columns = usable_dimensions + peer_measures
        if not feature_columns:
            continue

        working = df[feature_columns + [target_measure]].copy()
        working[target_measure] = pd.to_numeric(working[target_measure], errors='coerce')
        working = working.dropna(subset=[target_measure])
        if len(working) < 30 or working[target_measure].nunique() < 8:
            continue

        categorical_features = [column for column in usable_dimensions if column in working.columns]
        numeric_features = [column for column in peer_measures if column in working.columns]
        if not categorical_features and not numeric_features:
            continue

        preprocessor = ColumnTransformer(
            transformers=[
                ('cat', Pipeline([
                    ('imputer', SimpleImputer(strategy='most_frequent')),
                    ('encoder', OneHotEncoder(handle_unknown='ignore')),
                ]), categorical_features),
                ('num', Pipeline([
                    ('imputer', SimpleImputer(strategy='median')),
                ]), numeric_features),
            ],
            remainder='drop',
        )

        model = RandomForestRegressor(
            n_estimators=180,
            random_state=42,
            min_samples_leaf=3,
        )

        pipeline = Pipeline([
            ('preprocess', preprocessor),
            ('model', model),
        ])

        try:
            pipeline.fit(working[feature_columns], working[target_measure])
        except Exception:
            continue

        r_squared = float(pipeline.score(working[feature_columns], working[target_measure]))
        if not np.isfinite(r_squared) or r_squared < 0.2:
            continue

        cat_names = []
        if categorical_features:
            encoder = pipeline.named_steps['preprocess'].named_transformers_['cat'].named_steps['encoder']
            cat_names = list(encoder.get_feature_names_out(categorical_features))
        feature_names = cat_names + numeric_features
        importances = pipeline.named_steps['model'].feature_importances_
        if not len(feature_names) or not len(importances):
            continue

        aggregated: Dict[str, float] = {}
        for name, importance in zip(feature_names, importances):
            matched_column = next((column for column in categorical_features if name.startswith(f'{column}_')), name)
            aggregated[matched_column] = aggregated.get(matched_column, 0.0) + float(importance)

        ranked = sorted(aggregated.items(), key=lambda item: item[1], reverse=True)
        if not ranked:
            continue
        top_feature, top_importance = ranked[0]
        if top_importance < 0.18:
            continue

        chart_type = 'bar' if top_feature in categorical_features else 'scatter'
        chart_columns = [top_feature, target_measure] if chart_type == 'scatter' else [top_feature, target_measure]

        findings.append(enrich_with_statistics({
            'kind': 'model_driver',
            'title': f'{top_feature} looks like the strongest modeled driver of {target_measure}',
            'detail': f'The driver model explains about {r_squared * 100:.1f}% of variation in {target_measure}, with {top_feature} contributing the highest importance ({top_importance * 100:.1f}%).',
            'score': round(top_importance * 100, 3),
            'stat': f'R² = {r_squared:.2f} / top importance = {top_importance * 100:.1f}%',
            'recommended_chart': {
                'title': f'{target_measure} by {top_feature}',
                'description': 'Review the strongest modeled driver alongside the target metric to validate the pattern visually.',
                'type': chart_type,
                'columns': chart_columns,
                'sample_percentage': 100,
            }
        }, [
            'A lightweight random-forest model was fitted on the current dataset using the strongest segment columns and peer numeric drivers.',
            'This is directional and not causal, but it helps prioritize which factors deserve deeper analyst review next.',
        ], sample_size=len(working), effect_size=max(r_squared, top_importance), stability=min(1.0, r_squared)))

    findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
    return findings[:1]


def build_executive_takeaways(insights: List[Dict[str, Any]], quality_alerts: List[Dict[str, Any]]) -> List[str]:
    takeaways: List[str] = []
    if quality_alerts:
        top_alert = quality_alerts[0]
        takeaways.append(f"Data quality note: {top_alert['title']} - {top_alert['detail']}")

    ranked = sorted(
        insights,
        key=lambda item: (item.get('priority_score', 0), item.get('confidence_score', 0), item.get('score', 0)),
        reverse=True,
    )
    for insight in ranked[:3]:
        confidence = insight.get('confidence_label')
        takeaways.append(f"{insight['title']} ({confidence} confidence): {insight['detail']}")

    return takeaways[:4]
