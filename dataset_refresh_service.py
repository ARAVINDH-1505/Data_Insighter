import os
from datetime import datetime
from typing import Any, Dict, List, Optional

import pandas as pd

from dataset_pipeline_service import build_dataset_from_record, supports_pipeline_rebuild
from file_utils import read_data_file
from workspace_store import get_dataset_record


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError:
        return None


def schema_snapshot(df: pd.DataFrame) -> Dict[str, Any]:
    return {
        'columns': df.columns.tolist(),
        'dtypes': {column: str(dtype) for column, dtype in df.dtypes.astype(str).items()},
    }


def schema_changes(previous: Optional[Dict[str, Any]], current_df: pd.DataFrame) -> Dict[str, Any]:
    previous = previous or {'columns': [], 'dtypes': {}}
    current = schema_snapshot(current_df)
    previous_columns = previous.get('columns', [])
    current_columns = current.get('columns', [])

    previous_dtypes = previous.get('dtypes', {})
    current_dtypes = current.get('dtypes', {})

    changed_types = []
    for column in set(previous_columns) & set(current_columns):
        if previous_dtypes.get(column) != current_dtypes.get(column):
            changed_types.append({
                'column': column,
                'previous': previous_dtypes.get(column),
                'current': current_dtypes.get(column),
            })

    return {
        'added_columns': [column for column in current_columns if column not in previous_columns],
        'removed_columns': [column for column in previous_columns if column not in current_columns],
        'changed_types': changed_types,
        'current': current,
    }


def dataset_freshness(username: str, record: Dict[str, Any]) -> Dict[str, Any]:
    source_type = record.get('source_type')
    updated_at = _parse_timestamp(record.get('updated_at'))
    refresh_at = _parse_timestamp(record.get('metadata', {}).get('last_refreshed_at'))
    freshness = {
        'status': 'current',
        'reason': 'No newer upstream changes were detected.',
    }

    if source_type in {'upload', 'sample'} and record.get('stored_path') and os.path.exists(record['stored_path']):
        comparison_point = refresh_at or updated_at
        file_modified = datetime.fromtimestamp(
            os.path.getmtime(record['stored_path']),
            tz=comparison_point.tzinfo if comparison_point else None,
        )
        if comparison_point and file_modified > comparison_point:
            freshness = {
                'status': 'stale_source',
                'reason': 'The underlying source file changed after the dataset metadata was last refreshed.',
            }
    elif source_type in {'derived', 'joined', 'rebuilt'} and record.get('parent_dataset_id'):
        parent = get_dataset_record(username, record['parent_dataset_id'])
        parent_updated = _parse_timestamp(parent.get('updated_at')) if parent else None
        if parent_updated and updated_at and parent_updated > updated_at:
            freshness = {
                'status': 'upstream_changed',
                'reason': 'An upstream dataset version changed after this derived dataset was last updated.',
            }
    return freshness


def refresh_dataset_frame(username: str, record: Dict[str, Any]) -> pd.DataFrame:
    source_table = record.get('metadata', {}).get('source_table')
    if record.get('source_type') in {'upload', 'sample'}:
        return read_data_file(record['stored_path'], source_table=source_table)
    if supports_pipeline_rebuild(record):
        return build_dataset_from_record(username, record)
    if record.get('stored_path') and os.path.exists(record['stored_path']):
        return read_data_file(record['stored_path'], source_table=source_table)
    raise ValueError('This dataset cannot be refreshed because its source definition is incomplete.')
