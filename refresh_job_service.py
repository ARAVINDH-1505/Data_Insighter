import os
import re
import secrets
import socket
import threading
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from dataset_pipeline_service import build_dataset_from_record, supports_pipeline_rebuild
from dataset_refresh_service import schema_changes, schema_snapshot
from dataset_runtime import clear_runtime_cache
from file_utils import read_data_file
from time_utils import utcnow
from workspace_store import (
    create_refresh_job_record,
    get_dataset_record,
    get_refresh_job_record,
    list_all_refresh_job_records,
    list_refresh_job_records,
    update_dataset_record,
    update_refresh_job_record,
)


_SCHEDULER_LOCK = threading.Lock()
_SCHEDULER_THREAD: Optional[threading.Thread] = None
_RUNNER_LOCK = threading.Lock()
LEASE_SECONDS = 240


def _utcnow() -> datetime:
    return utcnow()


def _isoformat(value: datetime) -> str:
    return value.isoformat() + 'Z'


def _parse_timestamp(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).replace(tzinfo=None)
    except ValueError:
        return None


def _safe_stem(value: str) -> str:
    normalized = re.sub(r'[^a-zA-Z0-9_-]+', '_', (value or '').strip())
    return (normalized or 'dataset')[:80]


def _source_path(record: Dict[str, Any]) -> str:
    metadata = record.get('metadata') or {}
    return metadata.get('source_path') or record.get('stored_path')


def _managed_dataset_path(managed_dir: str, record: Dict[str, Any]) -> str:
    dataset_name = (record.get('metadata') or {}).get('display_name') or record.get('source_name') or record['id']
    filename = f"{_safe_stem(dataset_name)}_{record['id']}.parquet"
    return os.path.join(managed_dir, filename)


def worker_id(prefix: str = 'worker') -> str:
    return f"{prefix}:{socket.gethostname()}:{os.getpid()}:{secrets.token_hex(4)}"


def _runtime_metadata(job: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(job.get('metadata') or {})
    return dict(metadata.get('worker_runtime') or {})


def _with_runtime(job: Dict[str, Any], runtime: Dict[str, Any]) -> Dict[str, Any]:
    metadata = dict(job.get('metadata') or {})
    metadata['worker_runtime'] = runtime
    return metadata


def _lease_is_active(runtime: Dict[str, Any], reference_time: Optional[datetime] = None) -> bool:
    reference_time = reference_time or _utcnow()
    lease_until = _parse_timestamp(runtime.get('lease_expires_at'))
    return bool(lease_until and lease_until > reference_time and runtime.get('claimed_by'))


def _claim_job(job: Dict[str, Any], runner_id: str, lease_seconds: int = LEASE_SECONDS) -> Optional[Dict[str, Any]]:
    runtime = _runtime_metadata(job)
    if _lease_is_active(runtime) and runtime.get('claimed_by') != runner_id:
        return None

    now = _utcnow()
    claimed_runtime = {
        'claimed_by': runner_id,
        'claimed_at': _isoformat(now),
        'heartbeat_at': _isoformat(now),
        'lease_expires_at': _isoformat(now + timedelta(seconds=lease_seconds)),
    }
    return update_refresh_job_record(
        job['owner'],
        job['id'],
        {
            'last_status': 'running',
            'metadata': _with_runtime(job, claimed_runtime),
        },
    )


def _clear_claim(job: Dict[str, Any], runner_id: str, status: str) -> Dict[str, Any]:
    runtime = _runtime_metadata(job)
    now = _utcnow()
    cleared_runtime = {
        **runtime,
        'claimed_by': None,
        'lease_expires_at': None,
        'heartbeat_at': _isoformat(now),
        'last_worker_id': runner_id,
        'last_completed_at': _isoformat(now),
        'last_status': status,
    }
    return update_refresh_job_record(
        job['owner'],
        job['id'],
        {
            'metadata': _with_runtime(job, cleared_runtime),
        },
    )


def _write_frame(df: pd.DataFrame, target_path: str) -> None:
    os.makedirs(os.path.dirname(target_path), exist_ok=True)
    extension = os.path.splitext(target_path)[1].lower()
    if extension == '.parquet':
        df.to_parquet(target_path, index=False)
        return
    df.to_csv(target_path, index=False)


def _load_frame(path: str, record: Dict[str, Any]) -> pd.DataFrame:
    source_table = (record.get('metadata') or {}).get('source_table')
    return read_data_file(path, source_table=source_table)


def ensure_managed_materialization(record: Dict[str, Any], managed_dir: str) -> Dict[str, Any]:
    metadata = dict(record.get('metadata') or {})
    source_path = _source_path(record)
    if not source_path or not os.path.exists(source_path):
        raise ValueError('The source file for this dataset is no longer available.')

    materialized_path = metadata.get('materialized_path') or _managed_dataset_path(managed_dir, record)
    needs_refresh = (
        not metadata.get('source_path')
        or os.path.abspath(record.get('stored_path', '')) != os.path.abspath(materialized_path)
        or not os.path.exists(materialized_path)
    )
    if not needs_refresh:
        return record

    source_df = _load_frame(source_path, record)
    _write_frame(source_df, materialized_path)
    updated_metadata = {
        **metadata,
        'source_path': source_path,
        'materialized_path': materialized_path,
        'schema_snapshot': schema_snapshot(source_df),
        'last_refreshed_at': _isoformat(_utcnow()),
    }
    updated_record = update_dataset_record(
        record['owner'],
        record['id'],
        {
            'stored_path': materialized_path,
            'row_count': int(len(source_df)),
            'column_count': int(len(source_df.columns)),
            'metadata': updated_metadata,
        },
    )
    clear_runtime_cache()
    return updated_record


def _incremental_append(
    current_df: pd.DataFrame,
    source_df: pd.DataFrame,
    incremental_column: Optional[str],
) -> Tuple[pd.DataFrame, Dict[str, Any]]:
    if not incremental_column:
        return source_df, {
            'mode': 'full_fallback',
            'reason': 'Incremental refresh requires a cursor column.',
            'rows_added': int(len(source_df)),
        }
    if incremental_column not in source_df.columns or incremental_column not in current_df.columns:
        return source_df, {
            'mode': 'full_fallback',
            'reason': f"Incremental column '{incremental_column}' is not available in both snapshots.",
            'rows_added': int(len(source_df)),
        }
    if list(current_df.columns) != list(source_df.columns):
        return source_df, {
            'mode': 'full_fallback',
            'reason': 'Schema drift changed column order or shape, so a full refresh was used.',
            'rows_added': int(len(source_df)),
        }

    current_series = current_df[incremental_column]
    source_series = source_df[incremental_column]
    details = {
        'mode': 'incremental',
        'incremental_column': incremental_column,
    }

    if pd.api.types.is_numeric_dtype(source_series):
        current_values = pd.to_numeric(current_series, errors='coerce')
        source_values = pd.to_numeric(source_series, errors='coerce')
        max_value = current_values.dropna().max()
        delta = source_df[source_values > max_value] if pd.notna(max_value) else source_df.copy()
        details['cursor'] = None if pd.isna(max_value) else float(max_value)
    else:
        parsed_current = pd.to_datetime(current_series, errors='coerce')
        parsed_source = pd.to_datetime(source_series, errors='coerce')
        if parsed_source.notna().sum() and parsed_current.notna().sum():
            max_value = parsed_current.dropna().max()
            delta = source_df[parsed_source > max_value] if pd.notna(max_value) else source_df.copy()
            details['cursor'] = None if pd.isna(max_value) else max_value.isoformat()
        else:
            existing_values = set(current_series.dropna().astype(str))
            delta = source_df[~source_series.astype(str).isin(existing_values)]
            details['cursor'] = len(existing_values)

    combined = pd.concat([current_df, delta], ignore_index=True)
    combined = combined.drop_duplicates().reset_index(drop=True)
    details['rows_added'] = int(len(combined) - len(current_df))
    details['reason'] = 'Only newly detected rows were appended to the existing materialized dataset.'
    return combined, details


def perform_dataset_refresh(
    username: str,
    record: Dict[str, Any],
    managed_dir: str,
    mode: Optional[str] = None,
    incremental_column: Optional[str] = None,
) -> Tuple[Dict[str, Any], pd.DataFrame, Dict[str, Any], Dict[str, Any]]:
    metadata = dict(record.get('metadata') or {})
    refresh_policy = dict(metadata.get('refresh_policy') or {})
    resolved_mode = (mode or refresh_policy.get('mode') or 'full').lower()
    resolved_column = incremental_column or refresh_policy.get('incremental_column')
    target_record = record

    if resolved_mode == 'incremental':
        target_record = ensure_managed_materialization(record, managed_dir)
        source_df = _load_frame(_source_path(target_record), target_record)
        current_df = _load_frame(target_record['stored_path'], target_record)
        refreshed_df, refresh_details = _incremental_append(current_df, source_df, resolved_column)
        target_path = target_record['stored_path']
    elif record.get('source_type') in {'upload', 'sample'} or metadata.get('source_path'):
        target_record = ensure_managed_materialization(record, managed_dir)
        refreshed_df = _load_frame(_source_path(target_record), target_record)
        target_path = target_record['stored_path']
        refresh_details = {
            'mode': 'full',
            'rows_added': int(len(refreshed_df)),
            'reason': 'A full refresh replaced the managed dataset snapshot.',
        }
    elif supports_pipeline_rebuild(record):
        refreshed_df = build_dataset_from_record(username, record)
        target_path = record['stored_path']
        refresh_details = {
            'mode': 'pipeline_rebuild',
            'rows_added': int(len(refreshed_df)),
            'reason': 'The dataset was rebuilt from its saved transform pipeline.',
        }
    elif record.get('stored_path') and os.path.exists(record['stored_path']):
        refreshed_df = _load_frame(record['stored_path'], record)
        target_path = record['stored_path']
        refresh_details = {
            'mode': 'full',
            'rows_added': int(len(refreshed_df)),
            'reason': 'The current stored dataset snapshot was re-read as a full refresh.',
        }
    else:
        raise ValueError('This dataset cannot be refreshed because its source definition is incomplete.')

    if refreshed_df.empty:
        raise ValueError('The refreshed dataset is empty, so the refresh was cancelled.')

    _write_frame(refreshed_df, target_path)
    clear_runtime_cache()

    diff = schema_changes(metadata.get('schema_snapshot'), refreshed_df)
    refresh_event = {
        'operation': 'refresh_dataset',
        'kind': 'system',
        'mode': refresh_details['mode'],
        'description': f"Refreshed dataset materialization with {len(refreshed_df)} rows and {len(refreshed_df.columns)} columns.",
        'created_at': _isoformat(_utcnow()),
    }
    refresh_history = (metadata.get('refresh_history') or [])[-9:] + [refresh_event]
    updated_metadata = {
        **metadata,
        'columns': refreshed_df.columns.tolist(),
        'schema_snapshot': diff['current'],
        'last_refreshed_at': _isoformat(_utcnow()),
        'last_schema_change': {
            'added_columns': diff['added_columns'],
            'removed_columns': diff['removed_columns'],
            'changed_types': diff['changed_types'],
        },
        'refresh_history': refresh_history,
        'lineage_steps': metadata.get('lineage_steps', []) + [refresh_event],
        'source_path': metadata.get('source_path') or _source_path(target_record),
        'materialized_path': target_path,
        'refresh_policy': {
            **refresh_policy,
            'mode': resolved_mode,
            'incremental_column': resolved_column,
        },
        'last_refresh_mode': refresh_details['mode'],
    }
    updated_record = update_dataset_record(
        target_record['owner'],
        target_record['id'],
        {
            'stored_path': target_path,
            'row_count': int(len(refreshed_df)),
            'column_count': int(len(refreshed_df.columns)),
            'metadata': updated_metadata,
        },
    )
    return updated_record, refreshed_df, diff, refresh_details


def create_refresh_schedule(
    username: str,
    record: Dict[str, Any],
    managed_dir: str,
    cadence_minutes: int,
    mode: str = 'full',
    incremental_column: Optional[str] = None,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    prepared_record = record
    if mode == 'incremental':
        prepared_record = ensure_managed_materialization(record, managed_dir)

    metadata = dict(prepared_record.get('metadata') or {})
    updated_record = update_dataset_record(
        prepared_record['owner'],
        prepared_record['id'],
        {
            'metadata': {
                **metadata,
                'refresh_policy': {
                    **(metadata.get('refresh_policy') or {}),
                    'mode': mode,
                    'incremental_column': incremental_column,
                    'cadence_minutes': int(cadence_minutes),
                },
            }
        },
    )
    job = create_refresh_job_record(
        username,
        dataset_id=updated_record['id'],
        cadence_minutes=cadence_minutes,
        mode=mode,
        incremental_column=incremental_column,
        metadata={
            'source_dataset_name': updated_record.get('metadata', {}).get('display_name') or updated_record.get('source_name'),
        },
    )
    return updated_record, job


def list_refresh_schedules(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return list_refresh_job_records(username, dataset_id=dataset_id)


def due_refresh_jobs(reference_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
    reference_time = reference_time or _utcnow()
    due_jobs = []
    for job in list_all_refresh_job_records():
        if not job.get('enabled', True):
            continue
        if _lease_is_active(_runtime_metadata(job), reference_time):
            continue
        next_run_at = _parse_timestamp(job.get('next_run_at')) or _parse_timestamp(job.get('updated_at'))
        if next_run_at and next_run_at <= reference_time:
            due_jobs.append(job)
    return due_jobs


def run_refresh_job(job: Dict[str, Any], managed_dir: str, runner_id: Optional[str] = None) -> Dict[str, Any]:
    runner_id = runner_id or worker_id('manual')
    record = get_dataset_record(job['owner'], job['dataset_id'])
    now = _utcnow()
    if not record:
        updated_job = update_refresh_job_record(
            job['owner'],
            job['id'],
            {
                'last_run_at': _isoformat(now),
                'last_status': 'failed',
                'last_error': 'Dataset record no longer exists.',
                'next_run_at': _isoformat(now + timedelta(minutes=int(job.get('cadence_minutes', 60)))),
                'metadata': _with_runtime(job, {
                    **_runtime_metadata(job),
                    'claimed_by': None,
                    'lease_expires_at': None,
                    'heartbeat_at': _isoformat(now),
                    'last_worker_id': runner_id,
                    'last_status': 'failed',
                }),
            },
        )
        return {'success': False, 'job': updated_job, 'error': 'Dataset record no longer exists.'}

    try:
        updated_record, refreshed_df, diff, refresh_details = perform_dataset_refresh(
            job['owner'],
            record,
            managed_dir,
            mode=job.get('mode'),
            incremental_column=job.get('incremental_column'),
        )
        updated_job = update_refresh_job_record(
            job['owner'],
            job['id'],
            {
                'last_run_at': _isoformat(now),
                'last_status': 'succeeded',
                'last_error': None,
                'next_run_at': _isoformat(now + timedelta(minutes=int(job.get('cadence_minutes', 60)))),
                'metadata': {
                    **(job.get('metadata') or {}),
                    'last_schema_change': diff,
                    'last_run_details': refresh_details,
                    'worker_runtime': {
                        **_runtime_metadata(job),
                        'claimed_by': None,
                        'lease_expires_at': None,
                        'heartbeat_at': _isoformat(now),
                        'last_worker_id': runner_id,
                        'last_completed_at': _isoformat(now),
                        'last_status': 'succeeded',
                    },
                },
            },
        )
        return {
            'success': True,
            'job': updated_job,
            'dataset': updated_record,
            'rows': int(len(refreshed_df)),
            'schema_changes': diff,
            'refresh_details': refresh_details,
        }
    except Exception as exc:
        updated_job = update_refresh_job_record(
            job['owner'],
            job['id'],
            {
                'last_run_at': _isoformat(now),
                'last_status': 'failed',
                'last_error': str(exc),
                'next_run_at': _isoformat(now + timedelta(minutes=int(job.get('cadence_minutes', 60)))),
                'metadata': {
                    **(job.get('metadata') or {}),
                    'worker_runtime': {
                        **_runtime_metadata(job),
                        'claimed_by': None,
                        'lease_expires_at': None,
                        'heartbeat_at': _isoformat(now),
                        'last_worker_id': runner_id,
                        'last_completed_at': _isoformat(now),
                        'last_status': 'failed',
                    },
                },
            },
        )
        return {'success': False, 'job': updated_job, 'error': str(exc)}


def run_refresh_job_by_id(username: str, job_id: str, managed_dir: str) -> Dict[str, Any]:
    job = get_refresh_job_record(username, job_id)
    if not job:
        raise ValueError('Refresh schedule not found.')
    claimed = _claim_job(job, worker_id('manual'))
    return run_refresh_job(claimed or job, managed_dir, runner_id=(claimed or job).get('metadata', {}).get('worker_runtime', {}).get('claimed_by'))


def run_due_refresh_jobs(managed_dir: str, runner_id: Optional[str] = None, max_jobs: Optional[int] = None) -> List[Dict[str, Any]]:
    results = []
    runner_id = runner_id or worker_id('embedded')
    with _RUNNER_LOCK:
        for job in due_refresh_jobs():
            claimed_job = _claim_job(job, runner_id)
            if not claimed_job:
                continue
            results.append(run_refresh_job(claimed_job, managed_dir, runner_id=runner_id))
            if max_jobs is not None and len(results) >= max_jobs:
                break
    return results


def run_refresh_worker(
    managed_dir: str,
    poll_seconds: int = 30,
    iterations: Optional[int] = None,
    runner_id: Optional[str] = None,
) -> str:
    runner_id = runner_id or worker_id('worker')
    completed = 0
    while iterations is None or completed < iterations:
        run_due_refresh_jobs(managed_dir, runner_id=runner_id)
        completed += 1
        if iterations is not None and completed >= iterations:
            break
        threading.Event().wait(poll_seconds)
    return runner_id


def refresh_worker_summary(dataset_id: Optional[str] = None) -> Dict[str, Any]:
    jobs = list_all_refresh_job_records(dataset_id=dataset_id)
    runtime_states = [_runtime_metadata(job) for job in jobs]
    active_claims = [state for state in runtime_states if _lease_is_active(state)]
    last_completed = sorted(
        [state.get('last_completed_at') for state in runtime_states if state.get('last_completed_at')],
        reverse=True,
    )
    return {
        'active_claims': len(active_claims),
        'last_completed_at': last_completed[0] if last_completed else None,
        'worker_ids': sorted({state.get('last_worker_id') for state in runtime_states if state.get('last_worker_id')}),
        'embedded_scheduler': bool(_SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive()),
    }


def start_refresh_scheduler(managed_dir: str, poll_seconds: int = 30) -> None:
    global _SCHEDULER_THREAD
    with _SCHEDULER_LOCK:
        if _SCHEDULER_THREAD and _SCHEDULER_THREAD.is_alive():
            return

        def _runner() -> None:
            while True:
                try:
                    run_due_refresh_jobs(managed_dir)
                except Exception:
                    pass
                threading.Event().wait(poll_seconds)

        _SCHEDULER_THREAD = threading.Thread(
            target=_runner,
            name='data-insighter-refresh-scheduler',
            daemon=True,
        )
        _SCHEDULER_THREAD.start()
