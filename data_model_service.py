from difflib import SequenceMatcher
from typing import Any, Dict, List
import warnings

import pandas as pd

from file_utils import read_data_file


def _safe_series(df: pd.DataFrame, column: str) -> pd.Series:
    return df[column].dropna().astype(str).str.strip()


def _normalized_join_key(series: pd.Series) -> pd.Series:
    non_null = series.dropna()
    if non_null.empty:
        return series

    stringified = non_null.astype(str).str.strip()
    numeric_values = pd.to_numeric(stringified, errors='coerce')
    if numeric_values.notna().mean() >= 0.9:
        return pd.to_numeric(series.astype(str).str.strip(), errors='coerce')

    with warnings.catch_warnings():
        warnings.simplefilter('ignore', UserWarning)
        datetime_values = pd.to_datetime(stringified, errors='coerce')
    if datetime_values.notna().mean() >= 0.9:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            return pd.to_datetime(series, errors='coerce')

    return series.map(lambda value: None if pd.isna(value) else str(value).strip())


def profile_key_candidates(df: pd.DataFrame) -> List[Dict[str, Any]]:
    candidates: List[Dict[str, Any]] = []
    row_count = max(len(df), 1)

    for column in df.columns:
        values = _safe_series(df, column)
        non_null_ratio = len(values) / row_count
        unique_ratio = values.nunique() / max(len(values), 1)
        name = column.lower()
        name_score = 0.2 if any(token in name for token in ('id', 'key', 'code', 'number', 'no')) else 0
        score = min(1.0, (unique_ratio * 0.65) + (non_null_ratio * 0.25) + name_score)

        if non_null_ratio >= 0.7 and unique_ratio >= 0.55:
            candidates.append({
                'column': column,
                'unique_ratio': round(unique_ratio, 3),
                'non_null_ratio': round(non_null_ratio, 3),
                'score': round(score, 3),
                'role_hint': 'primary_key' if unique_ratio >= 0.95 else 'foreign_key_candidate',
            })

    candidates.sort(key=lambda item: item['score'], reverse=True)
    return candidates[:8]


def _name_similarity(left: str, right: str) -> float:
    left_clean = left.lower().replace('_', '').replace('-', '').replace(' ', '')
    right_clean = right.lower().replace('_', '').replace('-', '').replace(' ', '')
    if left_clean == right_clean:
        return 1.0
    return SequenceMatcher(None, left_clean, right_clean).ratio()


def _overlap_score(left_values: pd.Series, right_values: pd.Series) -> float:
    left_sample = set(left_values.head(5000).tolist())
    right_sample = set(right_values.head(5000).tolist())
    if not left_sample or not right_sample:
        return 0.0
    return len(left_sample & right_sample) / min(len(left_sample), len(right_sample))


def suggest_relationships(dataset_records: List[Dict[str, Any]]) -> Dict[str, Any]:
    loaded: List[Dict[str, Any]] = []
    suggestions: List[Dict[str, Any]] = []

    for record in dataset_records:
        try:
            df = read_data_file(record['stored_path'])
        except Exception:
            continue

        loaded.append({
            'record': record,
            'df': df,
            'keys': profile_key_candidates(df),
        })

    for left_idx, left in enumerate(loaded):
        for right in loaded[left_idx + 1:]:
            for left_key in left['keys']:
                left_values = _safe_series(left['df'], left_key['column'])
                if left_values.empty:
                    continue

                for right_key in right['keys']:
                    right_values = _safe_series(right['df'], right_key['column'])
                    if right_values.empty:
                        continue

                    overlap = _overlap_score(left_values, right_values)
                    name_score = _name_similarity(left_key['column'], right_key['column'])
                    confidence = (overlap * 0.7) + (name_score * 0.2) + (min(left_key['score'], right_key['score']) * 0.1)
                    if confidence < 0.35:
                        continue

                    suggestions.append({
                        'left_dataset_id': left['record']['id'],
                        'left_dataset_name': left['record'].get('metadata', {}).get('display_name') or left['record']['source_name'],
                        'left_column': left_key['column'],
                        'right_dataset_id': right['record']['id'],
                        'right_dataset_name': right['record'].get('metadata', {}).get('display_name') or right['record']['source_name'],
                        'right_column': right_key['column'],
                        'confidence': round(confidence, 3),
                        'overlap_score': round(overlap, 3),
                        'name_similarity': round(name_score, 3),
                        'recommended_join': 'left' if left['record']['row_count'] >= right['record']['row_count'] else 'inner',
                    })

    suggestions.sort(key=lambda item: item['confidence'], reverse=True)
    datasets = [
        {
            'id': item['record']['id'],
            'name': item['record'].get('metadata', {}).get('display_name') or item['record']['source_name'],
            'rows': item['record']['row_count'],
            'columns': item['record']['column_count'],
            'key_candidates': item['keys'],
        }
        for item in loaded
    ]
    return {'datasets': datasets, 'suggestions': suggestions[:12]}


def join_dataframes(
    left_df: pd.DataFrame,
    right_df: pd.DataFrame,
    left_key: str,
    right_key: str,
    join_type: str = 'left',
) -> pd.DataFrame:
    if join_type not in {'left', 'inner', 'outer'}:
        raise ValueError('Join type must be left, inner, or outer.')

    if left_key not in left_df.columns:
        raise ValueError(f"Left key '{left_key}' was not found.")
    if right_key not in right_df.columns:
        raise ValueError(f"Right key '{right_key}' was not found.")

    left_working = left_df.copy()
    right_working = right_df.copy()
    left_join_key = '__normalized_left_join_key__'
    right_join_key = '__normalized_right_join_key__'
    left_working[left_join_key] = _normalized_join_key(left_working[left_key])
    right_working[right_join_key] = _normalized_join_key(right_working[right_key])

    joined = pd.merge(
        left_working,
        right_working,
        how=join_type,
        left_on=left_join_key,
        right_on=right_join_key,
        suffixes=('', '_related'),
    )
    joined = joined.drop(columns=[left_join_key, right_join_key], errors='ignore')
    return joined.reset_index(drop=True)


def join_datasets(
    left_record: Dict[str, Any],
    right_record: Dict[str, Any],
    left_key: str,
    right_key: str,
    join_type: str = 'left',
) -> pd.DataFrame:
    left_df = read_data_file(left_record['stored_path'])
    right_df = read_data_file(right_record['stored_path'])
    return join_dataframes(left_df, right_df, left_key, right_key, join_type)
