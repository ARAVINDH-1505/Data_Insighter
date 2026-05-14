import math
from typing import Any, Dict, List

import numpy as np
import pandas as pd
from scipy import stats

from dataset_runtime import load_prepared_dataframe, prepare_dataframe
from insight_engine import (
    anomaly_insights,
    build_executive_takeaways,
    contribution_insights,
    decomposition_insights,
    enrich_with_statistics,
    forecast_insights,
    funnel_insights,
    model_driver_insights,
    retention_cohort_insights,
    seasonality_insights,
    segment_driver_insights,
    variance_explanation_insights,
)
from semantic_model import infer_dataset_semantics, summarize_semantic_model


class DataProcessor:
    def __init__(
        self,
        filepath: str | None = None,
        dataframe: pd.DataFrame | None = None,
        semantic_overrides: Dict[str, Dict[str, Any]] | None = None,
    ):
        self.filepath = filepath
        self.semantic_overrides = semantic_overrides or {}
        self._semantic_profiles_cache: List[Dict[str, Any]] | None = None
        self._semantic_model_cache: Dict[str, Any] | None = None
        if dataframe is not None:
            self.df = prepare_dataframe(dataframe)
        elif filepath:
            self.df = self._load_data()
        else:
            raise ValueError("Provide either a filepath or a dataframe")

    def _load_data(self) -> pd.DataFrame:
        """Load data from file with enhanced format support and error handling."""
        try:
            return load_prepared_dataframe(self.filepath)
        except Exception as e:
            raise ValueError(f"Error loading data: {str(e)}")

    def _serialize_value(self, value: Any) -> Any:
        if pd.isna(value):
            return None
        if isinstance(value, (np.integer,)):
            return int(value)
        if isinstance(value, (np.floating, float)):
            if math.isfinite(float(value)):
                return round(float(value), 4)
            return None
        if isinstance(value, (np.bool_, bool)):
            return bool(value)
        if isinstance(value, (pd.Timestamp,)):
            return value.strftime('%Y-%m-%d %H:%M:%S')
        return str(value)

    def _safe_ratio(self, numerator: float, denominator: float) -> float:
        return round((numerator / denominator) * 100, 2) if denominator else 0.0

    def _numeric_columns(self) -> List[str]:
        return self.df.select_dtypes(include=[np.number]).columns.tolist()

    def _datetime_columns(self) -> List[str]:
        return self.df.select_dtypes(include=['datetime64[ns]', 'datetime64[ns, UTC]']).columns.tolist()

    def _categorical_columns(self) -> List[str]:
        excluded = set(self._numeric_columns()) | set(self._datetime_columns())
        candidates = [column for column in self.df.columns if column not in excluded]
        return candidates

    def _semantic_profiles(self) -> List[Dict[str, Any]]:
        if self._semantic_profiles_cache is None:
            self._semantic_profiles_cache = infer_dataset_semantics(self.df, self.semantic_overrides)
        return self._semantic_profiles_cache

    def _semantic_model_summary(self) -> Dict[str, Any]:
        if self._semantic_model_cache is None:
            self._semantic_model_cache = summarize_semantic_model(self.df, self._semantic_profiles())
        return self._semantic_model_cache

    def _semantic_profile_map(self) -> Dict[str, Dict[str, Any]]:
        return {profile['name']: profile for profile in self._semantic_profiles()}

    def _measure_aggregation(self, column: str) -> str:
        profile = self._semantic_profile_map().get(column, {})
        return profile.get('default_aggregation') or 'sum'

    def _semantic_groups(self) -> Dict[str, List[str]]:
        groups: Dict[str, List[str]] = {
            'measure': [],
            'dimension': [],
            'datetime': [],
            'identifier': [],
        }
        for profile in self._semantic_profiles():
            groups.setdefault(profile['semantic_role'], []).append(profile['name'])
        return groups

    def _column_profiles(self) -> List[Dict[str, Any]]:
        profiles: List[Dict[str, Any]] = []

        for column in self.df.columns:
            series = self.df[column]
            non_null = series.dropna()
            profile = {
                'name': column,
                'dtype': str(series.dtype),
                'non_null_count': int(non_null.shape[0]),
                'missing_count': int(series.isna().sum()),
                'missing_pct': self._safe_ratio(series.isna().sum(), len(series)),
                'unique_count': int(non_null.nunique()),
                'sample_values': [self._serialize_value(value) for value in non_null.head(3).tolist()],
            }

            if pd.api.types.is_numeric_dtype(series):
                clean = non_null.astype(float)
                if not clean.empty:
                    q1 = clean.quantile(0.25)
                    q3 = clean.quantile(0.75)
                    iqr = q3 - q1
                    outlier_count = 0
                    if iqr > 0:
                        outlier_mask = (clean < (q1 - 1.5 * iqr)) | (clean > (q3 + 1.5 * iqr))
                        outlier_count = int(outlier_mask.sum())

                    profile.update({
                        'role': 'numeric',
                        'mean': self._serialize_value(clean.mean()),
                        'median': self._serialize_value(clean.median()),
                        'std': self._serialize_value(clean.std()),
                        'min': self._serialize_value(clean.min()),
                        'max': self._serialize_value(clean.max()),
                        'outlier_pct': self._safe_ratio(outlier_count, len(clean)),
                        'skewness': self._serialize_value(clean.skew()),
                    })
            elif pd.api.types.is_datetime64_any_dtype(series):
                profile.update({
                    'role': 'datetime',
                    'min': self._serialize_value(non_null.min() if not non_null.empty else None),
                    'max': self._serialize_value(non_null.max() if not non_null.empty else None),
                })
            else:
                top_counts = non_null.astype(str).value_counts().head(3)
                profile.update({
                    'role': 'categorical',
                    'top_values': [
                        {
                            'label': value,
                            'count': int(count),
                            'pct': self._safe_ratio(count, len(non_null)),
                        }
                        for value, count in top_counts.items()
                    ]
                })

            profiles.append(profile)

        return profiles

    def _quality_alerts(self) -> List[Dict[str, str]]:
        alerts: List[Dict[str, str]] = []
        row_count = len(self.df)
        duplicate_count = int(self.df.duplicated().sum())
        duplicate_pct = self._safe_ratio(duplicate_count, row_count)

        if duplicate_count:
            alerts.append({
                'severity': 'warning',
                'title': 'Duplicate rows detected',
                'detail': f'{duplicate_count} rows ({duplicate_pct}%) are exact duplicates and may distort aggregates.'
            })

        for profile in self._column_profiles():
            if profile['missing_pct'] >= 30:
                alerts.append({
                    'severity': 'warning',
                    'title': f"High missingness in '{profile['name']}'",
                    'detail': f"{profile['missing_pct']}% of this column is blank, so insights using it may be incomplete."
                })

            if profile.get('role') == 'numeric' and profile.get('outlier_pct', 0) >= 10:
                alerts.append({
                    'severity': 'info',
                    'title': f"Potential outliers in '{profile['name']}'",
                    'detail': f"{profile['outlier_pct']}% of values sit outside the IQR range. Review before using averages."
                })

        if not alerts:
            alerts.append({
                'severity': 'success',
                'title': 'No major data quality blockers found',
                'detail': 'The dataset looks usable for exploratory analysis with the current checks.'
            })

        return alerts[:8]

    def _correlation_insights(self) -> List[Dict[str, Any]]:
        numeric_columns = self._numeric_columns()
        if len(numeric_columns) < 2:
            return []

        findings = []

        for idx, left in enumerate(numeric_columns):
            for right in numeric_columns[idx + 1:]:
                pair = self.df[[left, right]].dropna()
                if len(pair) < 12:
                    continue
                value, p_value = stats.pearsonr(pair[left], pair[right])
                if pd.isna(value):
                    continue
                strength = abs(float(value))
                if strength >= 0.45 and (not np.isfinite(p_value) or p_value <= 0.1):
                    findings.append(enrich_with_statistics({
                        'kind': 'correlation',
                        'title': f'{left} and {right} move together',
                        'detail': f'The correlation is {value:.2f}, which is strong enough to investigate a business relationship.',
                        'score': round(strength, 3),
                        'stat': f'r = {value:.2f}',
                        'recommended_chart': {
                            'title': f'{left} vs {right}',
                            'description': 'Use a scatter plot to inspect the relationship and spot clusters or non-linear behavior.',
                            'type': 'scatter',
                            'columns': [left, right],
                            'sample_percentage': 100,
                        }
                    }, [
                        'Correlation is measured on rows where both numeric fields are present.',
                        'This surfaces linear relationships that are strong enough to influence forecasting, segmentation, or KPI interpretation.',
                    ], sample_size=len(pair), effect_size=strength, p_value=p_value))

        findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
        return findings[:3]

    def _distribution_insights(self) -> List[Dict[str, Any]]:
        findings = []
        for column in self._numeric_columns():
            series = self.df[column].dropna().astype(float)
            if len(series) < 8:
                continue

            skewness = series.skew()
            if pd.notna(skewness) and abs(float(skewness)) >= 1:
                direction = 'right-skewed' if skewness > 0 else 'left-skewed'
                findings.append(enrich_with_statistics({
                    'kind': 'distribution',
                    'title': f'{column} has a {direction} distribution',
                    'detail': 'The mean may be less representative than the median here, and a few values are likely stretching the range.',
                    'score': round(abs(float(skewness)), 3),
                    'stat': f'skew = {skewness:.2f}',
                    'recommended_chart': {
                        'title': f'{column} distribution',
                        'description': 'Inspect the distribution and the spread before making KPI claims from averages.',
                        'type': 'histogram',
                        'columns': [column],
                        'sample_percentage': 100,
                    }
                }, [
                    'Distribution shape is evaluated using skewness after dropping null values.',
                    'Highly skewed measures often need median-based interpretation or outlier review before being used in executive KPIs.',
                ], sample_size=len(series), effect_size=min(abs(float(skewness)) / 2.5, 2.0), stability=min(1.0, abs(float(skewness)) / 3)))

        findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
        return findings[:2]

    def _categorical_insights(self) -> List[Dict[str, Any]]:
        findings = []
        for column in self._categorical_columns():
            series = self.df[column].dropna().astype(str)
            if series.empty:
                continue

            unique_count = series.nunique()
            if unique_count < 2 or unique_count > 20:
                continue

            top_counts = series.value_counts()
            top_label = top_counts.index[0]
            top_count = int(top_counts.iloc[0])
            dominance = self._safe_ratio(top_count, len(series))
            entropy = stats.entropy(top_counts.values / top_counts.values.sum()) if len(top_counts) > 1 else 0

            if dominance >= 35:
                findings.append(enrich_with_statistics({
                    'kind': 'categorical',
                    'title': f"{column} is concentrated in '{top_label}'",
                    'detail': f"The top category contributes {dominance}% of observed rows, which suggests an imbalance worth explaining.",
                    'score': dominance,
                    'stat': f'{top_label}: {dominance}%',
                    'recommended_chart': {
                        'title': f'{column} composition',
                        'description': 'See whether one segment dominates the dataset or whether the distribution is balanced.',
                        'type': 'bar',
                        'columns': [column],
                        'sample_percentage': 100,
                    }
                }, [
                    'Category concentration is measured from the share of the top observed group.',
                    'Low diversity across categories can point to channel concentration, customer mix skew, or operational imbalance.',
                ], sample_size=len(series), effect_size=max(dominance / 100, 1 - min(float(entropy), 3.0) / 3.0), stability=min(1.0, dominance / 70)))

        findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
        return findings[:2]

    def _time_insights(self) -> List[Dict[str, Any]]:
        datetime_columns = self._datetime_columns()
        numeric_columns = self._numeric_columns()
        findings = []

        if not datetime_columns or not numeric_columns:
            return findings

        for date_column in datetime_columns[:2]:
            working = self.df[[date_column] + numeric_columns].dropna(subset=[date_column]).copy()
            if working.empty:
                continue

            working[date_column] = pd.to_datetime(working[date_column], errors='coerce')
            working = working.sort_values(date_column)
            for numeric_column in numeric_columns[:5]:
                series = working[[date_column, numeric_column]].dropna()
                if len(series) < 8:
                    continue
                aggregation = self._measure_aggregation(numeric_column)
                if aggregation == 'average':
                    trend_series = series.groupby(date_column)[numeric_column].mean().sort_index()
                else:
                    trend_series = series.groupby(date_column)[numeric_column].sum().sort_index()
                if len(trend_series) < 5:
                    continue

                x = np.arange(len(trend_series))
                regression = stats.linregress(x, trend_series.to_numpy(dtype=float))
                trend_delta = float(trend_series.iloc[-1] - trend_series.iloc[0])
                baseline = abs(float(trend_series.iloc[0])) or 1.0
                trend_pct = round((trend_delta / baseline) * 100, 2)
                slope_strength = abs(float(regression.rvalue)) if np.isfinite(regression.rvalue) else 0.0
                if slope_strength < 0.35 and abs(trend_pct) < 10:
                    continue
                findings.append(enrich_with_statistics({
                    'kind': 'trend',
                    'title': f'{numeric_column} changes over time',
                    'detail': f'From the first to last recorded period, the metric moves by {round(trend_delta, 2)} ({trend_pct}%).',
                    'score': abs(trend_pct) if abs(trend_pct) > abs(trend_delta) else abs(trend_delta),
                    'stat': f'change = {round(trend_delta, 2)} ({trend_pct}%)',
                    'recommended_chart': {
                        'title': f'{numeric_column} over time',
                        'description': 'Use a time-series view to inspect trend shifts, spikes, and seasonality.',
                        'type': 'line',
                        'columns': [date_column, numeric_column],
                        'sample_percentage': 100,
                    }
                }, [
                    'Trend direction is measured on period-level aggregates rather than raw row order.',
                    'The confidence score blends the number of observed periods with the consistency of the fitted trend.',
                ], sample_size=len(trend_series), effect_size=max(slope_strength, abs(trend_pct) / 100), p_value=regression.pvalue))

        findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('score', 0)), reverse=True)
        return findings[:2]

    def _recommended_visualizations(self) -> List[Dict[str, Any]]:
        recommendations: List[Dict[str, Any]] = []
        seen = set()

        for collection in (
            self._correlation_insights(),
            self._time_insights(),
            self._categorical_insights(),
            self._distribution_insights(),
        ):
            for insight in collection:
                chart = insight.get('recommended_chart')
                if not chart:
                    continue
                key = (chart['type'], tuple(chart['columns']))
                if key in seen:
                    continue
                seen.add(key)
                recommendations.append(chart)

        if not recommendations:
            numeric_columns = self._numeric_columns()
            categorical_columns = self._categorical_columns()
            if numeric_columns:
                recommendations.append({
                    'title': f'{numeric_columns[0]} distribution',
                    'description': 'Start by understanding the range, skew, and extreme values of a key metric.',
                    'type': 'histogram',
                    'columns': [numeric_columns[0]],
                    'sample_percentage': 100,
                })
            if categorical_columns:
                recommendations.append({
                    'title': f'{categorical_columns[0]} breakdown',
                    'description': 'Use this to understand how the dataset is distributed across categories.',
                    'type': 'bar',
                    'columns': [categorical_columns[0]],
                    'sample_percentage': 100,
                })

        numeric_columns = self._numeric_columns()
        categorical_columns = self._categorical_columns()
        if numeric_columns and not any(chart['type'] == 'kpi' for chart in recommendations):
            recommendations.insert(0, {
                'title': f'{numeric_columns[0]} KPI',
                'description': 'Start with a headline KPI card so the dashboard has a clear business metric.',
                'type': 'kpi',
                'columns': [numeric_columns[0]],
                'sample_percentage': 100,
            })
        if len(categorical_columns) >= 2 and not any(chart['type'] == 'heatmap' for chart in recommendations):
            recommendations.append({
                'title': f'{categorical_columns[0]} by {categorical_columns[1]} heatmap',
                'description': 'Use a heatmap to find concentration patterns across two dimensions.',
                'type': 'heatmap',
                'columns': categorical_columns[:2],
                'sample_percentage': 100,
            })

        return recommendations[:6]

    def _advanced_insights(self) -> List[Dict[str, Any]]:
        semantics = self._semantic_groups()
        measures = semantics.get('measure', [])
        dimensions = semantics.get('dimension', [])
        datetimes = semantics.get('datetime', [])
        identifiers = semantics.get('identifier', [])

        findings = (
            anomaly_insights(self.df, measures)
            + segment_driver_insights(self.df, dimensions, measures)
            + contribution_insights(self.df, dimensions, measures)
            + seasonality_insights(self.df, datetimes, measures)
            + forecast_insights(self.df, datetimes, measures)
            + decomposition_insights(self.df, datetimes, measures)
            + variance_explanation_insights(self.df, datetimes, dimensions, measures)
            + funnel_insights(self.df, measures)
            + model_driver_insights(self.df, dimensions, measures)
            + retention_cohort_insights(self.df, datetimes, identifiers)
        )
        findings.sort(key=lambda item: (item.get('priority_score', 0), item.get('confidence_score', 0), item.get('score', 0)), reverse=True)
        return findings[:6]

    def get_data_info(self) -> Dict[str, Any]:
        """Get a concise summary used by the upload flow."""
        return {
            'columns': self.df.columns.tolist(),
            'num_rows': len(self.df),
            'num_columns': len(self.df.columns),
            'dtypes': self.df.dtypes.astype(str).to_dict(),
            'preview': self.df.head(5).to_dict(orient='records'),
            'missing_values': self.df.isnull().sum().to_dict(),
            'numeric_columns': self._numeric_columns(),
            'categorical_columns': self._categorical_columns(),
            'datetime_columns': self._datetime_columns(),
        }

    def get_analysis_summary(self) -> Dict[str, Any]:
        """Generate a real, dataset-driven analysis summary for the insights UI."""
        row_count = len(self.df)
        duplicate_count = int(self.df.duplicated().sum())
        numeric_columns = self._numeric_columns()
        categorical_columns = self._categorical_columns()
        datetime_columns = self._datetime_columns()

        completeness = 100 - self._safe_ratio(self.df.isna().sum().sum(), max(row_count * max(len(self.df.columns), 1), 1))

        foundational_insights = (
            self._correlation_insights()
            + self._time_insights()
            + self._categorical_insights()
            + self._distribution_insights()
        )
        advanced_insights = self._advanced_insights()
        key_insights = sorted(
            foundational_insights + advanced_insights,
            key=lambda item: (item.get('priority_score', 0), item.get('confidence_score', 0), item.get('score', 0)),
            reverse=True,
        )[:8]
        semantic_profiles = self._semantic_profiles()
        semantic_model = self._semantic_model_summary()
        quality_alerts = self._quality_alerts()

        return {
            'dataset_overview': {
                'rows': row_count,
                'columns': int(len(self.df.columns)),
                'numeric_columns': int(len(numeric_columns)),
                'categorical_columns': int(len(categorical_columns)),
                'datetime_columns': int(len(datetime_columns)),
                'completeness_pct': round(completeness, 2),
                'duplicate_rows': duplicate_count,
                'duplicate_pct': self._safe_ratio(duplicate_count, row_count),
            },
            'quality_alerts': quality_alerts,
            'key_insights': key_insights,
            'recommended_visualizations': self._recommended_visualizations(),
            'column_profiles': self._column_profiles(),
            'semantic_profiles': semantic_profiles,
            'semantic_model': semantic_model,
            'executive_takeaways': build_executive_takeaways(key_insights, quality_alerts),
        }
