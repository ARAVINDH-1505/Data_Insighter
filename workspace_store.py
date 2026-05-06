import json
import os
import re
import secrets
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional


BASE_DIR = os.path.join(os.path.dirname(__file__), 'workspace_data')
DATASETS_DIR = os.path.join(BASE_DIR, 'datasets')
DASHBOARDS_DIR = os.path.join(BASE_DIR, 'dashboards')
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
RELATIONSHIPS_DIR = os.path.join(BASE_DIR, 'relationships')
MEASURES_DIR = os.path.join(BASE_DIR, 'measures')
AUDIT_DIR = os.path.join(BASE_DIR, 'audit')


def ensure_workspace_dirs() -> None:
    os.makedirs(DATASETS_DIR, exist_ok=True)
    os.makedirs(DASHBOARDS_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs(RELATIONSHIPS_DIR, exist_ok=True)
    os.makedirs(MEASURES_DIR, exist_ok=True)
    os.makedirs(AUDIT_DIR, exist_ok=True)


def _safe_user_segment(username: str) -> str:
    normalized = re.sub(r'[^a-zA-Z0-9_-]+', '_', username.strip())
    return normalized or 'default_user'


def _dataset_path(username: str, dataset_id: str) -> str:
    user_dir = os.path.join(DATASETS_DIR, _safe_user_segment(username))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, f'{dataset_id}.json')


def _list_user_dataset_files(username: str) -> List[str]:
    user_dir = os.path.join(DATASETS_DIR, _safe_user_segment(username))
    if not os.path.exists(user_dir):
        return []
    return [
        os.path.join(user_dir, filename)
        for filename in os.listdir(user_dir)
        if filename.endswith('.json')
    ]


def _list_all_dataset_files() -> List[str]:
    ensure_workspace_dirs()
    records: List[str] = []
    if not os.path.exists(DATASETS_DIR):
        return records

    for root, _, filenames in os.walk(DATASETS_DIR):
        for filename in filenames:
            if filename.endswith('.json'):
                records.append(os.path.join(root, filename))
    return records


def _dashboard_path(username: str, dashboard_id: str) -> str:
    user_dir = os.path.join(DASHBOARDS_DIR, _safe_user_segment(username))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, f'{dashboard_id}.json')


def _list_user_dashboard_files(username: str) -> List[str]:
    user_dir = os.path.join(DASHBOARDS_DIR, _safe_user_segment(username))
    if not os.path.exists(user_dir):
        return []
    return [
        os.path.join(user_dir, filename)
        for filename in os.listdir(user_dir)
        if filename.endswith('.json')
    ]


def _report_path(username: str, report_id: str) -> str:
    user_dir = os.path.join(REPORTS_DIR, _safe_user_segment(username))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, f'{report_id}.json')


def _list_user_report_files(username: str) -> List[str]:
    user_dir = os.path.join(REPORTS_DIR, _safe_user_segment(username))
    if not os.path.exists(user_dir):
        return []
    return [
        os.path.join(user_dir, filename)
        for filename in os.listdir(user_dir)
        if filename.endswith('.json')
    ]


def _relationship_path(username: str, relationship_id: str) -> str:
    user_dir = os.path.join(RELATIONSHIPS_DIR, _safe_user_segment(username))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, f'{relationship_id}.json')


def _list_user_relationship_files(username: str) -> List[str]:
    user_dir = os.path.join(RELATIONSHIPS_DIR, _safe_user_segment(username))
    if not os.path.exists(user_dir):
        return []
    return [
        os.path.join(user_dir, filename)
        for filename in os.listdir(user_dir)
        if filename.endswith('.json')
    ]


def _measure_path(username: str, measure_id: str) -> str:
    user_dir = os.path.join(MEASURES_DIR, _safe_user_segment(username))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, f'{measure_id}.json')


def _list_user_measure_files(username: str) -> List[str]:
    user_dir = os.path.join(MEASURES_DIR, _safe_user_segment(username))
    if not os.path.exists(user_dir):
        return []
    return [
        os.path.join(user_dir, filename)
        for filename in os.listdir(user_dir)
        if filename.endswith('.json')
    ]


def _audit_path(username: str, event_id: str) -> str:
    user_dir = os.path.join(AUDIT_DIR, _safe_user_segment(username))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, f'{event_id}.json')


def _list_user_audit_files(username: str) -> List[str]:
    user_dir = os.path.join(AUDIT_DIR, _safe_user_segment(username))
    if not os.path.exists(user_dir):
        return []
    return [
        os.path.join(user_dir, filename)
        for filename in os.listdir(user_dir)
        if filename.endswith('.json')
    ]


def _read_json(path: str) -> Optional[Dict[str, Any]]:
    try:
        with open(path, 'r', encoding='utf-8') as handle:
            return json.load(handle)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None


def _write_json_atomic(path: str, payload: Dict[str, Any]) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
            'w',
            encoding='utf-8',
            dir=os.path.dirname(path),
            delete=False,
        ) as handle:
            temp_path = handle.name
            json.dump(payload, handle, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    finally:
        if temp_path and os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass


def create_dataset_record(
    username: str,
    source_name: str,
    stored_path: str,
    source_type: str,
    row_count: int,
    column_count: int,
    parent_dataset_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    dataset_id = f"ds_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    now = datetime.utcnow().isoformat() + 'Z'

    record = {
        'id': dataset_id,
        'source_name': source_name,
        'stored_path': stored_path,
        'source_type': source_type,
        'row_count': int(row_count),
        'column_count': int(column_count),
        'parent_dataset_id': parent_dataset_id,
        'created_at': now,
        'updated_at': now,
        'metadata': metadata or {},
    }

    _write_json_atomic(_dataset_path(username, dataset_id), record)

    return record


def update_dataset_record(username: str, dataset_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_dataset_record(username, dataset_id)
    if not record:
        return None

    record.update(updates)
    record['updated_at'] = datetime.utcnow().isoformat() + 'Z'

    _write_json_atomic(_dataset_path(username, dataset_id), record)

    return record


def get_dataset_record(username: str, dataset_id: str) -> Optional[Dict[str, Any]]:
    ensure_workspace_dirs()
    return _read_json(_dataset_path(username, dataset_id))


def list_dataset_records(username: str) -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    records = []
    for path in _list_user_dataset_files(username):
        record = _read_json(path)
        if record:
            records.append(record)

    records.sort(key=lambda item: item.get('updated_at', ''), reverse=True)
    return records


def list_all_dataset_records() -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    records = []
    for path in _list_all_dataset_files():
        record = _read_json(path)
        if record:
            records.append(record)

    records.sort(key=lambda item: item.get('updated_at', ''), reverse=True)
    return records


def create_dashboard_record(
    username: str,
    name: str,
    dataset_id: Optional[str],
    dashboard_viz: List[Dict[str, Any]],
    dashboard_state: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    dashboard_id = f"db_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    now = datetime.utcnow().isoformat() + 'Z'
    record = {
        'id': dashboard_id,
        'name': name,
        'dataset_id': dataset_id,
        'created_at': now,
        'updated_at': now,
        'dashboard_viz': dashboard_viz,
        'dashboard_state': dashboard_state or {},
    }
    _write_json_atomic(_dashboard_path(username, dashboard_id), record)
    return record


def get_dashboard_record(username: str, dashboard_id: str) -> Optional[Dict[str, Any]]:
    ensure_workspace_dirs()
    return _read_json(_dashboard_path(username, dashboard_id))


def create_report_record(
    username: str,
    name: str,
    dataset_id: Optional[str],
    report_payload: Dict[str, Any],
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    report_id = f"rpt_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    now = datetime.utcnow().isoformat() + 'Z'
    record = {
        'id': report_id,
        'name': name,
        'dataset_id': dataset_id,
        'created_at': now,
        'updated_at': now,
        'report': report_payload,
    }
    _write_json_atomic(_report_path(username, report_id), record)
    return record


def get_report_record(username: str, report_id: str) -> Optional[Dict[str, Any]]:
    ensure_workspace_dirs()
    return _read_json(_report_path(username, report_id))


def list_report_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    records = []
    for path in _list_user_report_files(username):
        record = _read_json(path)
        if not record:
            continue
        if dataset_id and record.get('dataset_id') != dataset_id:
            continue
        records.append(record)

    records.sort(key=lambda item: item.get('updated_at', ''), reverse=True)
    return records


def list_dashboard_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    records = []
    for path in _list_user_dashboard_files(username):
        record = _read_json(path)
        if not record:
            continue
        if dataset_id and record.get('dataset_id') != dataset_id:
            continue
        records.append(record)

    records.sort(key=lambda item: item.get('updated_at', ''), reverse=True)
    return records


def create_relationship_record(
    username: str,
    left_dataset_id: str,
    left_column: str,
    right_dataset_id: str,
    right_column: str,
    join_type: str,
    confidence: Optional[float] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    relationship_id = f"rel_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    now = datetime.utcnow().isoformat() + 'Z'
    record = {
        'id': relationship_id,
        'left_dataset_id': left_dataset_id,
        'left_column': left_column,
        'right_dataset_id': right_dataset_id,
        'right_column': right_column,
        'join_type': join_type,
        'confidence': confidence,
        'created_at': now,
        'updated_at': now,
    }
    _write_json_atomic(_relationship_path(username, relationship_id), record)
    return record


def list_relationship_records(username: str) -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    records = []
    for path in _list_user_relationship_files(username):
        record = _read_json(path)
        if record:
            records.append(record)

    records.sort(key=lambda item: item.get('updated_at', ''), reverse=True)
    return records


def create_measure_record(
    username: str,
    dataset_id: Optional[str],
    name: str,
    definition: Dict[str, Any],
    latest_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    measure_id = f"msr_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    now = datetime.utcnow().isoformat() + 'Z'
    record = {
        'id': measure_id,
        'dataset_id': dataset_id,
        'name': name,
        'definition': definition,
        'latest_result': latest_result or {},
        'created_at': now,
        'updated_at': now,
    }
    _write_json_atomic(_measure_path(username, measure_id), record)
    return record


def list_measure_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    records = []
    for path in _list_user_measure_files(username):
        record = _read_json(path)
        if not record:
            continue
        if dataset_id and record.get('dataset_id') != dataset_id:
            continue
        records.append(record)

    records.sort(key=lambda item: item.get('updated_at', ''), reverse=True)
    return records


def log_audit_event(
    username: str,
    action: str,
    dataset_id: Optional[str] = None,
    artifact_type: str = 'dataset',
    artifact_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    event_id = f"evt_{datetime.utcnow().strftime('%Y%m%d%H%M%S')}_{secrets.token_hex(4)}"
    event = {
        'id': event_id,
        'action': action,
        'dataset_id': dataset_id,
        'artifact_type': artifact_type,
        'artifact_id': artifact_id,
        'details': details or {},
        'created_at': datetime.utcnow().isoformat() + 'Z',
    }
    _write_json_atomic(_audit_path(username, event_id), event)
    return event


def list_audit_events(
    username: str,
    dataset_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    events = []
    for path in _list_user_audit_files(username):
        event = _read_json(path)
        if not event:
            continue
        if dataset_id and event.get('dataset_id') != dataset_id:
            continue
        if artifact_id and event.get('artifact_id') != artifact_id:
            continue
        events.append(event)

    events.sort(key=lambda item: item.get('created_at', ''), reverse=True)
    return events[:limit]
