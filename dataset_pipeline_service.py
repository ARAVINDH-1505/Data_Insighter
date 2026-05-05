from typing import Any, Dict, List, Optional

import pandas as pd

from data_model_service import join_dataframes
from file_utils import read_data_file
from transform_service import apply_transform
from workspace_store import get_dataset_record


def dataset_pipeline_steps(record: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    if not record:
        return []
    return record.get('metadata', {}).get('pipeline_steps', [])


def supports_pipeline_rebuild(record: Optional[Dict[str, Any]]) -> bool:
    if not record:
        return False
    if record.get('source_type') in {'upload', 'sample'}:
        return True
    metadata = record.get('metadata', {})
    if record.get('source_type') == 'derived':
        return bool(metadata.get('pipeline_steps')) and bool(record.get('parent_dataset_id'))
    if record.get('source_type') == 'joined':
        join_meta = metadata.get('join') or {}
        return bool(join_meta.get('left_dataset_id') and join_meta.get('right_dataset_id'))
    if record.get('source_type') == 'rebuilt':
        return bool(record.get('parent_dataset_id'))
    return False


def build_dataset_from_record(
    username: str,
    record: Dict[str, Any],
    cache: Optional[Dict[str, pd.DataFrame]] = None,
) -> pd.DataFrame:
    if cache is None:
        cache = {}

    record_id = record['id']
    if record_id in cache:
        return cache[record_id].copy()

    source_type = record.get('source_type')
    metadata = record.get('metadata', {})

    if source_type in {'upload', 'sample'}:
        df = read_data_file(record['stored_path'])
    elif source_type == 'rebuilt':
        parent_id = record.get('parent_dataset_id')
        if not parent_id:
            raise ValueError('This rebuilt dataset has no parent definition.')
        parent_record = get_dataset_record(username, parent_id)
        if not parent_record:
            raise ValueError('The parent dataset for this rebuild could not be found.')
        df = build_dataset_from_record(username, parent_record, cache)
    elif source_type == 'derived':
        parent_id = record.get('parent_dataset_id')
        if not parent_id:
            raise ValueError('This derived dataset has no parent dataset.')
        parent_record = get_dataset_record(username, parent_id)
        if not parent_record:
            raise ValueError('The parent dataset for this transform could not be found.')
        parent_df = build_dataset_from_record(username, parent_record, cache)
        step = (metadata.get('pipeline_steps') or [{}])[-1]
        if step.get('kind') != 'transform':
            raise ValueError('This dataset does not contain a replayable transform definition.')
        df, _ = apply_transform(parent_df, step.get('operation'), step.get('options') or {})
    elif source_type == 'joined':
        join_meta = metadata.get('join') or {}
        left_record = get_dataset_record(username, join_meta.get('left_dataset_id', ''))
        right_record = get_dataset_record(username, join_meta.get('right_dataset_id', ''))
        if not left_record or not right_record:
            raise ValueError('One of the joined source datasets is no longer available.')
        left_df = build_dataset_from_record(username, left_record, cache)
        right_df = build_dataset_from_record(username, right_record, cache)
        df = join_dataframes(
            left_df,
            right_df,
            left_key=join_meta.get('left_column'),
            right_key=join_meta.get('right_column'),
            join_type=join_meta.get('join_type') or 'left',
        )
    else:
        if record.get('stored_path'):
            df = read_data_file(record['stored_path'])
        else:
            raise ValueError(f"Unsupported dataset source type for rebuild: {source_type}")

    cache[record_id] = df.copy()
    return df.copy()
