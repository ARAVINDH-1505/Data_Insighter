from __future__ import annotations

from typing import Any, Dict, List, Mapping

import numpy as np
import pandas as pd


GEOGRAPHY_HINTS = {'country', 'state', 'city', 'region', 'postal', 'zip', 'latitude', 'longitude', 'lat', 'lon'}
IDENTIFIER_HINTS = {'id', 'uuid', 'key', 'code', 'number', 'no', 'sku', 'ticket'}
CURRENCY_HINTS = {'revenue', 'sales', 'price', 'cost', 'profit', 'income', 'amount', 'spend', 'expense', 'gmv'}
PERCENT_HINTS = {'percent', 'percentage', 'pct', 'rate', 'ratio', 'share', 'conversion'}
COUNT_HINTS = {'count', 'qty', 'quantity', 'volume', 'units', 'visits', 'orders', 'sessions', 'users'}
DATE_HINTS = {'date', 'time', 'month', 'year', 'day', 'week', 'quarter'}
TEXT_HINTS = {'description', 'comment', 'notes', 'message', 'summary'}
BOOLEAN_HINTS = {'is_', 'has_', 'flag', 'enabled', 'active', 'status'}

HIERARCHY_TEMPLATES = {
    'calendar': ['year', 'quarter', 'month', 'week', 'day', 'date'],
    'geography': ['region', 'country', 'state', 'province', 'city', 'postal', 'zip'],
    'commerce': ['department', 'category', 'subcategory', 'brand', 'product', 'sku'],
    'organization': ['company', 'division', 'department', 'team', 'manager', 'employee'],
}

OVERRIDABLE_FIELDS = {
    'semantic_role',
    'subtype',
    'metric_family',
    'default_aggregation',
    'is_additive',
    'format_hint',
    'time_grain',
    'analysis_priority',
    'certified',
    'business_name',
    'description',
}


def _contains_hint(column_name: str, hints: set[str]) -> bool:
    lowered = column_name.lower()
    return any(hint in lowered for hint in hints)


def _cardinality_band(unique_count: int, row_count: int) -> str:
    if unique_count <= 1:
        return 'constant'
    ratio = unique_count / max(row_count, 1)
    if unique_count <= 12:
        return 'low'
    if unique_count <= 50 or ratio <= 0.15:
        return 'medium'
    if ratio >= 0.85:
        return 'very_high'
    return 'high'


def _looks_like_sequence(series: pd.Series) -> bool:
    clean = pd.to_numeric(series.dropna(), errors='coerce')
    if clean.empty or clean.isna().any():
        return False
    if not np.allclose(clean, np.round(clean)):
        return False
    sorted_values = np.sort(clean.astype(int).unique())
    if len(sorted_values) < 3:
        return False
    deltas = np.diff(sorted_values)
    return bool(np.all(deltas == deltas[0]) and deltas[0] in {1, -1})


def _infer_datetime_grain(series: pd.Series) -> str:
    clean = pd.to_datetime(series.dropna(), errors='coerce')
    clean = clean.dropna()
    if clean.empty or len(clean) < 2:
        return 'unknown'
    diffs = clean.sort_values().diff().dropna()
    if diffs.empty:
        return 'unknown'
    median_days = diffs.dt.total_seconds().median() / 86400
    if median_days < 1 / 24:
        return 'sub-hourly'
    if median_days < 1:
        return 'hourly'
    if median_days <= 2:
        return 'daily'
    if median_days <= 8:
        return 'weekly'
    if median_days <= 32:
        return 'monthly'
    if median_days <= 100:
        return 'quarterly'
    return 'yearly'


def _infer_metric_family(column_name: str, series: pd.Series) -> str:
    lowered = column_name.lower()
    if _contains_hint(lowered, PERCENT_HINTS):
        return 'percentage'
    if _contains_hint(lowered, CURRENCY_HINTS):
        return 'currency'
    if _contains_hint(lowered, COUNT_HINTS):
        return 'count'
    if pd.api.types.is_bool_dtype(series) or _contains_hint(lowered, BOOLEAN_HINTS):
        return 'boolean'
    return 'numeric'


def _default_aggregation(metric_family: str) -> str:
    if metric_family in {'percentage', 'boolean'}:
        return 'average'
    if metric_family == 'currency':
        return 'sum'
    if metric_family == 'count':
        return 'sum'
    return 'sum'


def _format_hint(subtype: str, metric_family: str) -> str:
    if subtype == 'date':
        return 'date'
    if metric_family == 'currency':
        return 'currency'
    if metric_family == 'percentage':
        return 'percentage'
    if metric_family == 'count':
        return 'integer'
    return 'number'


def _hierarchy_hint(column_name: str) -> Dict[str, Any] | None:
    lowered = column_name.lower()
    for hierarchy_name, levels in HIERARCHY_TEMPLATES.items():
        for idx, level in enumerate(levels):
            if level in lowered:
                next_levels = levels[idx + 1:] if idx + 1 < len(levels) else []
                return {
                    'name': hierarchy_name,
                    'level': level,
                    'next_levels': next_levels,
                }
    return None


def infer_semantic_type(column: str, series: pd.Series) -> Dict[str, Any]:
    non_null = series.dropna()
    row_count = max(len(series), 1)
    non_null_count = int(non_null.shape[0])
    unique_count = int(non_null.nunique()) if not non_null.empty else 0
    uniqueness_ratio = unique_count / max(len(non_null), 1)
    null_ratio = 1 - (non_null_count / row_count)
    lowered = column.lower()

    semantic_role = 'dimension'
    subtype = 'category'
    rationale: List[str] = []
    metric_family = 'categorical'
    default_aggregation = 'count'
    is_additive = False
    time_grain = None
    hierarchy = _hierarchy_hint(lowered)

    if pd.api.types.is_datetime64_any_dtype(series) or _contains_hint(lowered, DATE_HINTS):
        semantic_role = 'datetime'
        subtype = 'date'
        default_aggregation = 'none'
        time_grain = _infer_datetime_grain(series)
        rationale.append('The field is parsed as a date or its name strongly implies time-based values.')
        if time_grain and time_grain != 'unknown':
            rationale.append(f'Observed values suggest a primary {time_grain} grain.')
    elif pd.api.types.is_numeric_dtype(series):
        metric_family = _infer_metric_family(lowered, series)
        default_aggregation = _default_aggregation(metric_family)
        is_additive = metric_family in {'currency', 'count', 'numeric'}
        semantic_role = 'measure'
        subtype = metric_family
        rationale.append('The field behaves like a numeric measure suitable for aggregation.')

        if metric_family == 'percentage':
            rationale.append('Its name suggests a percentage, rate, or ratio, so averaging is safer than summing.')
            is_additive = False
        elif metric_family == 'currency':
            rationale.append('Its name suggests a financial amount, so sum is the default business aggregation.')
        elif metric_family == 'count':
            rationale.append('Its name suggests a volume metric that is usually additive across records.')

        if ((_contains_hint(lowered, IDENTIFIER_HINTS) and uniqueness_ratio > 0.9) or lowered.endswith('_id') or _looks_like_sequence(series)):
            semantic_role = 'identifier'
            subtype = 'identifier'
            metric_family = 'identifier'
            default_aggregation = 'count_distinct'
            is_additive = False
            rationale.append('Although numeric, the uniqueness pattern and naming make it behave like a row identifier.')
    else:
        if pd.api.types.is_bool_dtype(series) or _contains_hint(lowered, BOOLEAN_HINTS):
            semantic_role = 'dimension'
            subtype = 'boolean'
            rationale.append('The field behaves like a yes/no or status dimension.')
        elif ((_contains_hint(lowered, IDENTIFIER_HINTS) and uniqueness_ratio > 0.9) or lowered.endswith('_id')):
            semantic_role = 'identifier'
            subtype = 'identifier'
            default_aggregation = 'count_distinct'
            rationale.append('The field name and uniqueness pattern suggest row-level identifiers.')
        elif _contains_hint(lowered, GEOGRAPHY_HINTS):
            semantic_role = 'dimension'
            subtype = 'geography'
            rationale.append('The field name suggests a geographic dimension.')
        elif _contains_hint(lowered, TEXT_HINTS) and unique_count > max(20, int(row_count * 0.3)):
            semantic_role = 'dimension'
            subtype = 'text'
            rationale.append('The field looks like free text, so it is better treated as descriptive context than as a grouping key.')
        else:
            semantic_role = 'dimension'
            subtype = 'category'
            rationale.append('The field is best treated as a grouping dimension or segment.')

    cardinality_band = _cardinality_band(unique_count, row_count)
    if semantic_role == 'dimension' and cardinality_band == 'low':
        rationale.append('Its cardinality is low enough to work well in grouped comparisons and filters.')
    elif semantic_role == 'dimension' and cardinality_band in {'high', 'very_high'}:
        rationale.append('Its cardinality is high, so ranking, search, or filtering will work better than raw charting.')
    if hierarchy:
        rationale.append(f"It fits the {hierarchy['name']} hierarchy at the {hierarchy['level']} level.")

    analysis_priority = 50
    if semantic_role == 'measure':
        analysis_priority = 90 if unique_count > 1 else 40
        if metric_family == 'currency':
            analysis_priority = 95
        elif metric_family == 'percentage':
            analysis_priority = 82
    elif semantic_role == 'datetime':
        analysis_priority = 88
    elif semantic_role == 'dimension':
        analysis_priority = 80 if cardinality_band in {'low', 'medium'} else 58
    elif semantic_role == 'identifier':
        analysis_priority = 42

    return {
        'name': column,
        'semantic_role': semantic_role,
        'subtype': subtype,
        'metric_family': metric_family,
        'unique_count': unique_count,
        'uniqueness_ratio': round(uniqueness_ratio, 4),
        'null_ratio': round(null_ratio, 4),
        'non_null_count': non_null_count,
        'default_aggregation': default_aggregation,
        'is_additive': is_additive,
        'format_hint': _format_hint(subtype, metric_family),
        'cardinality_band': cardinality_band,
        'analysis_priority': analysis_priority,
        'time_grain': time_grain,
        'hierarchy': hierarchy,
        'rationale': rationale,
        'certified': False,
        'business_name': column,
        'description': '',
        'override_source': 'inferred',
    }


def _clean_override(column: str, override: Mapping[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(override, Mapping):
        return {}

    cleaned = {
        key: value
        for key, value in override.items()
        if key in OVERRIDABLE_FIELDS and value not in {None, ''}
    }
    cleaned['name'] = column
    return cleaned


def apply_semantic_overrides(
    profiles: List[Dict[str, Any]],
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    if not overrides:
        return profiles

    applied_profiles: List[Dict[str, Any]] = []
    override_map = {
        column: _clean_override(column, override)
        for column, override in overrides.items()
    }

    for profile in profiles:
        merged = dict(profile)
        override = override_map.get(profile['name']) or {}
        if override:
            for field, value in override.items():
                if field == 'name':
                    continue
                merged[field] = value
            merged['override_source'] = 'manual'
            merged['rationale'] = [
                *(profile.get('rationale') or []),
                'The semantic layer was manually adjusted in the workspace and should be treated as the current business definition.',
            ]
        applied_profiles.append(merged)

    return applied_profiles


def infer_dataset_semantics(
    df: pd.DataFrame,
    overrides: Mapping[str, Mapping[str, Any]] | None = None,
) -> List[Dict[str, Any]]:
    profiles = [infer_semantic_type(column, df[column]) for column in df.columns]
    return apply_semantic_overrides(profiles, overrides)


def summarize_semantic_model(df: pd.DataFrame, profiles: List[Dict[str, Any]] | None = None) -> Dict[str, Any]:
    profiles = profiles or infer_dataset_semantics(df)
    measures = [profile for profile in profiles if profile['semantic_role'] == 'measure']
    dimensions = [profile for profile in profiles if profile['semantic_role'] == 'dimension']
    datetimes = [profile for profile in profiles if profile['semantic_role'] == 'datetime']
    identifiers = [profile for profile in profiles if profile['semantic_role'] == 'identifier']

    grain_candidates = [
        profile['name']
        for profile in identifiers
        if profile.get('uniqueness_ratio', 0) >= 0.9 and profile.get('null_ratio', 1) <= 0.2
    ]
    if not grain_candidates:
        grain_candidates = [
            profile['name']
            for profile in dimensions
            if profile.get('cardinality_band') == 'very_high' and profile.get('null_ratio', 1) <= 0.1
        ][:2]

    date_hierarchies = [
        {
            'column': profile['name'],
            'grain': profile.get('time_grain') or 'unknown',
            'levels': ['year', 'quarter', 'month', 'week', 'day'],
        }
        for profile in datetimes
    ]

    dataset_role = 'dimension'
    if measures and (len(df) >= 25 or len(measures) >= 2):
        dataset_role = 'fact'
    elif measures and dimensions:
        dataset_role = 'hybrid'
    elif identifiers and dimensions:
        dataset_role = 'lookup'

    warnings: List[str] = []
    if not measures:
        warnings.append('No numeric business measures were detected, so dashboards may lean more toward profiling than KPI analysis.')
    if not dimensions:
        warnings.append('No low-friction grouping dimensions were detected, which limits comparison-style insights.')
    if len(datetimes) > 1:
        warnings.append('Multiple date fields were detected. Choose a primary time axis when building trend views.')
    if not grain_candidates:
        warnings.append('The row grain is not obvious yet. Review identifiers before trusting distinct counts or joins.')

    recommended_measures = [
        profile['name']
        for profile in sorted(measures, key=lambda item: item.get('analysis_priority', 0), reverse=True)[:4]
    ]
    recommended_dimensions = [
        profile['name']
        for profile in sorted(dimensions, key=lambda item: item.get('analysis_priority', 0), reverse=True)
        if profile.get('cardinality_band') in {'low', 'medium'}
    ][:4]

    semantic_coverage = {
        'measures': len(measures),
        'dimensions': len(dimensions),
        'datetime': len(datetimes),
        'identifiers': len(identifiers),
    }
    certified_metrics = [profile['name'] for profile in measures if profile.get('certified')]
    manual_override_count = len([profile for profile in profiles if profile.get('override_source') == 'manual'])

    return {
        'dataset_role': dataset_role,
        'row_grain': grain_candidates[0] if len(grain_candidates) == 1 else (' + '.join(grain_candidates) if grain_candidates else 'unspecified'),
        'grain_candidates': grain_candidates,
        'primary_date_column': datetimes[0]['name'] if datetimes else None,
        'date_hierarchies': date_hierarchies,
        'recommended_measures': recommended_measures,
        'recommended_dimensions': recommended_dimensions,
        'semantic_coverage': semantic_coverage,
        'warnings': warnings,
        'certified_metrics': certified_metrics,
        'manual_override_count': manual_override_count,
    }
