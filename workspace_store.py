import json
import os
import re
import secrets
import sqlite3
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from time_utils import utcnow_iso, utcnow_stamp


BASE_DIR = os.path.join(os.path.dirname(__file__), 'workspace_data')
DATASETS_DIR = os.path.join(BASE_DIR, 'datasets')
DASHBOARDS_DIR = os.path.join(BASE_DIR, 'dashboards')
REPORTS_DIR = os.path.join(BASE_DIR, 'reports')
QUERIES_DIR = os.path.join(BASE_DIR, 'queries')
RELATIONSHIPS_DIR = os.path.join(BASE_DIR, 'relationships')
MEASURES_DIR = os.path.join(BASE_DIR, 'measures')
AUDIT_DIR = os.path.join(BASE_DIR, 'audit')


TABLE_DEFINITIONS = {
    'workspace_meta': '''
        CREATE TABLE IF NOT EXISTS workspace_meta (
            key TEXT PRIMARY KEY,
            value TEXT
        )
    ''',
    'datasets': '''
        CREATE TABLE IF NOT EXISTS datasets (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
    'dashboards': '''
        CREATE TABLE IF NOT EXISTS dashboards (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
    'reports': '''
        CREATE TABLE IF NOT EXISTS reports (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
    'queries': '''
        CREATE TABLE IF NOT EXISTS queries (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
    'relationships': '''
        CREATE TABLE IF NOT EXISTS relationships (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
    'measures': '''
        CREATE TABLE IF NOT EXISTS measures (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
    'audit_events': '''
        CREATE TABLE IF NOT EXISTS audit_events (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
    'refresh_jobs': '''
        CREATE TABLE IF NOT EXISTS refresh_jobs (
            id TEXT PRIMARY KEY,
            owner TEXT NOT NULL,
            updated_at TEXT,
            dataset_id TEXT,
            artifact_id TEXT,
            payload TEXT NOT NULL
        )
    ''',
}

TABLE_INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_datasets_owner_updated ON datasets(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dashboards_owner_updated ON dashboards(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_dashboards_dataset ON dashboards(dataset_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reports_owner_updated ON reports(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_reports_dataset ON reports(dataset_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_queries_owner_updated ON queries(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_queries_dataset ON queries(dataset_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_relationships_owner_updated ON relationships(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_measures_owner_updated ON measures(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_measures_dataset ON measures(dataset_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_owner_updated ON audit_events(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_dataset ON audit_events(dataset_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_audit_artifact ON audit_events(artifact_id, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_refresh_jobs_owner_updated ON refresh_jobs(owner, updated_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_refresh_jobs_dataset ON refresh_jobs(dataset_id, updated_at DESC)",
)

def ensure_workspace_dirs() -> None:
    os.makedirs(DATASETS_DIR, exist_ok=True)
    os.makedirs(DASHBOARDS_DIR, exist_ok=True)
    os.makedirs(REPORTS_DIR, exist_ok=True)
    os.makedirs(QUERIES_DIR, exist_ok=True)
    os.makedirs(RELATIONSHIPS_DIR, exist_ok=True)
    os.makedirs(MEASURES_DIR, exist_ok=True)
    os.makedirs(AUDIT_DIR, exist_ok=True)
    _initialize_workspace_db()


def _safe_user_segment(username: str) -> str:
    normalized = re.sub(r'[^a-zA-Z0-9_-]+', '_', username.strip())
    return normalized or 'default_user'


def _owner_from_record_path(base_dir: str, path: str) -> Optional[str]:
    try:
        relative = os.path.relpath(path, base_dir)
        parts = relative.split(os.sep)
        if len(parts) >= 2:
            return parts[0]
    except ValueError:
        return None
    return None


def _inject_owner(record: Optional[Dict[str, Any]], owner: Optional[str]) -> Optional[Dict[str, Any]]:
    if not record:
        return None
    hydrated = dict(record)
    if owner and not hydrated.get('owner'):
        hydrated['owner'] = owner
    return hydrated


def _workspace_db_path() -> str:
    return os.path.join(BASE_DIR, 'workspace.db')


def _connect() -> sqlite3.Connection:
    os.makedirs(BASE_DIR, exist_ok=True)
    connection = sqlite3.connect(_workspace_db_path())
    connection.row_factory = sqlite3.Row
    return connection


def _initialize_workspace_db() -> None:
    with _connect() as connection:
        connection.execute('PRAGMA journal_mode=WAL')
        for ddl in TABLE_DEFINITIONS.values():
            connection.execute(ddl)
        for ddl in TABLE_INDEXES:
            connection.execute(ddl)
        if not _meta_value(connection, 'legacy_json_migrated'):
            _migrate_legacy_json_records(connection)
            _set_meta_value(connection, 'legacy_json_migrated', '1')
        connection.commit()


def _meta_value(connection: sqlite3.Connection, key: str) -> Optional[str]:
    row = connection.execute(
        'SELECT value FROM workspace_meta WHERE key = ?',
        (key,),
    ).fetchone()
    return row['value'] if row else None


def _set_meta_value(connection: sqlite3.Connection, key: str, value: str) -> None:
    connection.execute(
        'INSERT INTO workspace_meta(key, value) VALUES(?, ?) '
        'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
        (key, value),
    )


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


def _query_path(username: str, query_id: str) -> str:
    user_dir = os.path.join(QUERIES_DIR, _safe_user_segment(username))
    os.makedirs(user_dir, exist_ok=True)
    return os.path.join(user_dir, f'{query_id}.json')


def _list_user_query_files(username: str) -> List[str]:
    user_dir = os.path.join(QUERIES_DIR, _safe_user_segment(username))
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


def _record_timestamp(table: str, record: Dict[str, Any]) -> str:
    if table == 'audit_events':
        return record.get('created_at') or utcnow_iso()
    return record.get('updated_at') or record.get('created_at') or utcnow_iso()


def _record_dataset_id(record: Dict[str, Any]) -> Optional[str]:
    return record.get('dataset_id')


def _record_artifact_id(table: str, record: Dict[str, Any]) -> Optional[str]:
    if table == 'audit_events':
        return record.get('artifact_id')
    return record.get('id')


def _upsert_record(table: str, owner: str, record: Dict[str, Any]) -> Dict[str, Any]:
    ensure_workspace_dirs()
    hydrated = _inject_owner(record, owner)
    with _connect() as connection:
        connection.execute(
            f'INSERT INTO {table}(id, owner, updated_at, dataset_id, artifact_id, payload) '
            'VALUES(?, ?, ?, ?, ?, ?) '
            'ON CONFLICT(id) DO UPDATE SET '
            'owner = excluded.owner, '
            'updated_at = excluded.updated_at, '
            'dataset_id = excluded.dataset_id, '
            'artifact_id = excluded.artifact_id, '
            'payload = excluded.payload',
            (
                hydrated['id'],
                owner,
                _record_timestamp(table, hydrated),
                _record_dataset_id(hydrated),
                _record_artifact_id(table, hydrated),
                json.dumps(hydrated, indent=2),
            ),
        )
        connection.commit()
    return hydrated


def _fetch_record(table: str, owner: str, record_id: str) -> Optional[Dict[str, Any]]:
    ensure_workspace_dirs()
    with _connect() as connection:
        row = connection.execute(
            f'SELECT payload FROM {table} WHERE owner = ? AND id = ?',
            (owner, record_id),
        ).fetchone()
    if not row:
        return None
    return _inject_owner(json.loads(row['payload']), owner)


def _list_records(
    table: str,
    owner: Optional[str] = None,
    dataset_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> List[Dict[str, Any]]:
    ensure_workspace_dirs()
    clauses: List[str] = []
    parameters: List[Any] = []

    if owner:
        clauses.append('owner = ?')
        parameters.append(owner)
    if dataset_id:
        clauses.append('dataset_id = ?')
        parameters.append(dataset_id)
    if artifact_id:
        clauses.append('artifact_id = ?')
        parameters.append(artifact_id)

    query = f'SELECT owner, payload FROM {table}'
    if clauses:
        query += ' WHERE ' + ' AND '.join(clauses)
    query += ' ORDER BY updated_at DESC'
    if limit is not None:
        query += ' LIMIT ?'
        parameters.append(limit)

    with _connect() as connection:
        rows = connection.execute(query, parameters).fetchall()

    return [
        _inject_owner(json.loads(row['payload']), row['owner'])
        for row in rows
    ]


def _migrate_legacy_json_records(connection: sqlite3.Connection) -> None:
    legacy_table_dirs = {
        'datasets': DATASETS_DIR,
        'dashboards': DASHBOARDS_DIR,
        'reports': REPORTS_DIR,
        'queries': QUERIES_DIR,
        'relationships': RELATIONSHIPS_DIR,
        'measures': MEASURES_DIR,
        'audit_events': AUDIT_DIR,
    }
    legacy_sources = {
        'datasets': _list_all_dataset_files(),
        'dashboards': [
            path
            for root, _, filenames in os.walk(DASHBOARDS_DIR)
            for path in [
                os.path.join(root, filename)
                for filename in filenames
                if filename.endswith('.json')
            ]
        ] if os.path.exists(DASHBOARDS_DIR) else [],
        'reports': [
            path
            for root, _, filenames in os.walk(REPORTS_DIR)
            for path in [
                os.path.join(root, filename)
                for filename in filenames
                if filename.endswith('.json')
            ]
        ] if os.path.exists(REPORTS_DIR) else [],
        'queries': [
            path
            for root, _, filenames in os.walk(QUERIES_DIR)
            for path in [
                os.path.join(root, filename)
                for filename in filenames
                if filename.endswith('.json')
            ]
        ] if os.path.exists(QUERIES_DIR) else [],
        'relationships': [
            path
            for root, _, filenames in os.walk(RELATIONSHIPS_DIR)
            for path in [
                os.path.join(root, filename)
                for filename in filenames
                if filename.endswith('.json')
            ]
        ] if os.path.exists(RELATIONSHIPS_DIR) else [],
        'measures': [
            path
            for root, _, filenames in os.walk(MEASURES_DIR)
            for path in [
                os.path.join(root, filename)
                for filename in filenames
                if filename.endswith('.json')
            ]
        ] if os.path.exists(MEASURES_DIR) else [],
        'audit_events': [
            path
            for root, _, filenames in os.walk(AUDIT_DIR)
            for path in [
                os.path.join(root, filename)
                for filename in filenames
                if filename.endswith('.json')
            ]
        ] if os.path.exists(AUDIT_DIR) else [],
    }

    for table, paths in legacy_sources.items():
        legacy_dir = legacy_table_dirs.get(table)
        for path in paths:
            record = _read_json(path)
            if not record or not record.get('id'):
                continue
            owner = record.get('owner') or _owner_from_record_path(legacy_dir, path)
            if not owner:
                continue
            payload = json.dumps(_inject_owner(record, owner), indent=2)
            connection.execute(
                f'INSERT OR IGNORE INTO {table}(id, owner, updated_at, dataset_id, artifact_id, payload) '
                'VALUES(?, ?, ?, ?, ?, ?)',
                (
                    record['id'],
                    owner,
                    _record_timestamp(table, record),
                    _record_dataset_id(record),
                    _record_artifact_id(table, record),
                    payload,
                ),
            )


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
    dataset_id = f"ds_{utcnow_stamp()}_{secrets.token_hex(4)}"
    now = utcnow_iso()

    record = {
        'id': dataset_id,
        'owner': username,
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

    return _upsert_record('datasets', username, record)


def update_dataset_record(username: str, dataset_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_dataset_record(username, dataset_id)
    if not record:
        return None

    record.update(updates)
    record['updated_at'] = utcnow_iso()

    return _upsert_record('datasets', username, record)


def get_dataset_record(username: str, dataset_id: str) -> Optional[Dict[str, Any]]:
    return _fetch_record('datasets', username, dataset_id)


def list_dataset_records(username: str) -> List[Dict[str, Any]]:
    return _list_records('datasets', owner=username)


def list_all_dataset_records() -> List[Dict[str, Any]]:
    return _list_records('datasets')


def create_dashboard_record(
    username: str,
    name: str,
    dataset_id: Optional[str],
    dashboard_viz: List[Dict[str, Any]],
    dashboard_state: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    dashboard_id = f"db_{utcnow_stamp()}_{secrets.token_hex(4)}"
    now = utcnow_iso()
    record = {
        'id': dashboard_id,
        'owner': username,
        'name': name,
        'dataset_id': dataset_id,
        'created_at': now,
        'updated_at': now,
        'dashboard_viz': dashboard_viz,
        'dashboard_state': dashboard_state or {},
        'metadata': metadata or {},
    }
    return _upsert_record('dashboards', username, record)


def update_dashboard_record(username: str, dashboard_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_dashboard_record(username, dashboard_id)
    if not record:
        return None
    record.update(updates)
    record['updated_at'] = utcnow_iso()
    return _upsert_record('dashboards', username, record)


def get_dashboard_record(username: str, dashboard_id: str) -> Optional[Dict[str, Any]]:
    return _fetch_record('dashboards', username, dashboard_id)


def list_dashboard_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('dashboards', owner=username, dataset_id=dataset_id)


def list_all_dashboard_records(dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('dashboards', dataset_id=dataset_id)


def create_report_record(
    username: str,
    name: str,
    dataset_id: Optional[str],
    report_payload: Dict[str, Any],
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    report_id = f"rpt_{utcnow_stamp()}_{secrets.token_hex(4)}"
    now = utcnow_iso()
    record = {
        'id': report_id,
        'owner': username,
        'name': name,
        'dataset_id': dataset_id,
        'created_at': now,
        'updated_at': now,
        'report': report_payload,
        'metadata': metadata or {},
    }
    return _upsert_record('reports', username, record)


def update_report_record(username: str, report_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_report_record(username, report_id)
    if not record:
        return None
    record.update(updates)
    record['updated_at'] = utcnow_iso()
    return _upsert_record('reports', username, record)


def get_report_record(username: str, report_id: str) -> Optional[Dict[str, Any]]:
    return _fetch_record('reports', username, report_id)


def list_report_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('reports', owner=username, dataset_id=dataset_id)


def list_all_report_records(dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('reports', dataset_id=dataset_id)


def create_query_record(
    username: str,
    name: str,
    dataset_id: Optional[str],
    sql: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    query_id = f"qry_{utcnow_stamp()}_{secrets.token_hex(4)}"
    now = utcnow_iso()
    record = {
        'id': query_id,
        'owner': username,
        'name': name,
        'dataset_id': dataset_id,
        'sql': sql,
        'created_at': now,
        'updated_at': now,
        'metadata': metadata or {},
    }
    return _upsert_record('queries', username, record)


def update_query_record(username: str, query_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_query_record(username, query_id)
    if not record:
        return None
    record.update(updates)
    record['updated_at'] = utcnow_iso()
    return _upsert_record('queries', username, record)


def get_query_record(username: str, query_id: str) -> Optional[Dict[str, Any]]:
    return _fetch_record('queries', username, query_id)


def list_query_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('queries', owner=username, dataset_id=dataset_id)


def list_all_query_records(dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('queries', dataset_id=dataset_id)


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
    relationship_id = f"rel_{utcnow_stamp()}_{secrets.token_hex(4)}"
    now = utcnow_iso()
    record = {
        'id': relationship_id,
        'owner': username,
        'left_dataset_id': left_dataset_id,
        'left_column': left_column,
        'right_dataset_id': right_dataset_id,
        'right_column': right_column,
        'join_type': join_type,
        'confidence': confidence,
        'created_at': now,
        'updated_at': now,
    }
    return _upsert_record('relationships', username, record)


def list_relationship_records(username: str) -> List[Dict[str, Any]]:
    return _list_records('relationships', owner=username)


def list_all_relationship_records() -> List[Dict[str, Any]]:
    return _list_records('relationships')


def create_measure_record(
    username: str,
    dataset_id: Optional[str],
    name: str,
    definition: Dict[str, Any],
    latest_result: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    measure_id = f"msr_{utcnow_stamp()}_{secrets.token_hex(4)}"
    now = utcnow_iso()
    record = {
        'id': measure_id,
        'owner': username,
        'dataset_id': dataset_id,
        'name': name,
        'definition': definition,
        'latest_result': latest_result or {},
        'created_at': now,
        'updated_at': now,
    }
    return _upsert_record('measures', username, record)


def list_measure_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('measures', owner=username, dataset_id=dataset_id)


def list_all_measure_records(dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('measures', dataset_id=dataset_id)


def log_audit_event(
    username: str,
    action: str,
    dataset_id: Optional[str] = None,
    artifact_type: str = 'dataset',
    artifact_id: Optional[str] = None,
    details: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    event_id = f"evt_{utcnow_stamp()}_{secrets.token_hex(4)}"
    event = {
        'id': event_id,
        'owner': username,
        'action': action,
        'dataset_id': dataset_id,
        'artifact_type': artifact_type,
        'artifact_id': artifact_id,
        'details': details or {},
        'created_at': utcnow_iso(),
    }
    return _upsert_record('audit_events', username, event)


def list_audit_events(
    username: str,
    dataset_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    return _list_records(
        'audit_events',
        owner=username,
        dataset_id=dataset_id,
        artifact_id=artifact_id,
        limit=limit,
    )


def list_all_audit_events(
    dataset_id: Optional[str] = None,
    artifact_id: Optional[str] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    return _list_records(
        'audit_events',
        dataset_id=dataset_id,
        artifact_id=artifact_id,
        limit=limit,
    )


def create_refresh_job_record(
    username: str,
    dataset_id: str,
    cadence_minutes: int,
    mode: str = 'full',
    incremental_column: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    ensure_workspace_dirs()
    job_id = f"job_{utcnow_stamp()}_{secrets.token_hex(4)}"
    now = utcnow_iso()
    next_run_at = utcnow_iso()
    record = {
        'id': job_id,
        'owner': username,
        'dataset_id': dataset_id,
        'cadence_minutes': int(cadence_minutes),
        'mode': mode,
        'incremental_column': incremental_column,
        'enabled': True,
        'created_at': now,
        'updated_at': now,
        'next_run_at': next_run_at,
        'last_run_at': None,
        'last_status': 'scheduled',
        'last_error': None,
        'metadata': metadata or {},
    }
    return _upsert_record('refresh_jobs', username, record)


def update_refresh_job_record(username: str, job_id: str, updates: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    record = get_refresh_job_record(username, job_id)
    if not record:
        return None
    record.update(updates)
    record['updated_at'] = utcnow_iso()
    return _upsert_record('refresh_jobs', username, record)


def get_refresh_job_record(username: str, job_id: str) -> Optional[Dict[str, Any]]:
    return _fetch_record('refresh_jobs', username, job_id)


def list_refresh_job_records(username: str, dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('refresh_jobs', owner=username, dataset_id=dataset_id)


def list_all_refresh_job_records(dataset_id: Optional[str] = None) -> List[Dict[str, Any]]:
    return _list_records('refresh_jobs', dataset_id=dataset_id)
