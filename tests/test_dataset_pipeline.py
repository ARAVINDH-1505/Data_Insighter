import os

import app as app_module
import workspace_store
from dataset_pipeline_service import build_dataset_from_record


def _configure_workspace_dirs(tmp_path, monkeypatch):
    base_dir = tmp_path / 'workspace_data'
    monkeypatch.setattr(workspace_store, 'BASE_DIR', str(base_dir))
    monkeypatch.setattr(workspace_store, 'DATASETS_DIR', str(base_dir / 'datasets'))
    monkeypatch.setattr(workspace_store, 'DASHBOARDS_DIR', str(base_dir / 'dashboards'))
    monkeypatch.setattr(workspace_store, 'RELATIONSHIPS_DIR', str(base_dir / 'relationships'))
    monkeypatch.setattr(workspace_store, 'MEASURES_DIR', str(base_dir / 'measures'))
    workspace_store.ensure_workspace_dirs()


def _set_session(client, dataset_id=None, filepath=None, token='test-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = 'analyst'
        if dataset_id:
            session_state['current_dataset_id'] = dataset_id
        if filepath:
            session_state['current_filepath'] = filepath
    return token


def test_build_dataset_from_record_replays_transform(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)

    source = tmp_path / 'customers.csv'
    source.write_text('name,score\n  Alice  ,10\nBob,20\n', encoding='utf-8')

    root_record = workspace_store.create_dataset_record(
        'analyst',
        source_name='customers.csv',
        stored_path=str(source),
        source_type='upload',
        row_count=2,
        column_count=2,
        metadata={
            'display_name': 'customers.csv',
            'columns': ['name', 'score'],
            'lineage_steps': [],
            'pipeline_steps': [],
        },
    )
    derived_record = workspace_store.create_dataset_record(
        'analyst',
        source_name='customers_trimmed.csv',
        stored_path=str(tmp_path / 'customers_trimmed.csv'),
        source_type='derived',
        row_count=2,
        column_count=2,
        parent_dataset_id=root_record['id'],
        metadata={
            'display_name': 'customers.csv / transformed',
            'columns': ['name', 'score'],
            'lineage_steps': [],
            'pipeline_steps': [
                {
                    'kind': 'transform',
                    'operation': 'trim_text',
                    'description': 'Trimmed leading and trailing whitespace from text columns.',
                    'options': {},
                    'created_at': '2026-01-01T00:00:00Z',
                }
            ],
        },
    )

    rebuilt = build_dataset_from_record('analyst', derived_record)

    assert rebuilt['name'].tolist() == ['Alice', 'Bob']


def test_pipeline_routes_support_undo_and_rebuild(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    app_module.app.config['TESTING'] = True
    original_upload_folder = app_module.app.config['UPLOAD_FOLDER']
    app_module.app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    os.makedirs(app_module.app.config['UPLOAD_FOLDER'], exist_ok=True)

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\n North ,100\nSouth,200\n', encoding='utf-8')
    transformed_source = tmp_path / 'sales_trimmed.csv'
    transformed_source.write_text('region,revenue\nNorth,100\nSouth,200\n', encoding='utf-8')

    root_record = workspace_store.create_dataset_record(
        'analyst',
        source_name='sales.csv',
        stored_path=str(source),
        source_type='upload',
        row_count=2,
        column_count=2,
        metadata={
            'display_name': 'sales.csv',
            'columns': ['region', 'revenue'],
            'lineage_steps': [],
            'pipeline_steps': [],
        },
    )
    derived_record = workspace_store.create_dataset_record(
        'analyst',
        source_name='sales_trimmed.csv',
        stored_path=str(transformed_source),
        source_type='derived',
        row_count=2,
        column_count=2,
        parent_dataset_id=root_record['id'],
        metadata={
            'display_name': 'sales.csv / transformed',
            'columns': ['region', 'revenue'],
            'lineage_steps': [
                {
                    'kind': 'transform',
                    'operation': 'trim_text',
                    'description': 'Trimmed leading and trailing whitespace from text columns.',
                    'options': {},
                    'created_at': '2026-01-01T00:00:00Z',
                }
            ],
            'pipeline_steps': [
                {
                    'kind': 'transform',
                    'operation': 'trim_text',
                    'description': 'Trimmed leading and trailing whitespace from text columns.',
                    'options': {},
                    'created_at': '2026-01-01T00:00:00Z',
                }
            ],
        },
    )

    try:
        with app_module.app.test_client() as client:
            token = _set_session(client, dataset_id=derived_record['id'], filepath=str(transformed_source))

            pipeline_response = client.get(f"/datasets/{derived_record['id']}/pipeline")
            pipeline_payload = pipeline_response.get_json()
            assert pipeline_response.status_code == 200
            assert pipeline_payload['pipeline']['can_undo'] is True
            assert pipeline_payload['pipeline']['can_rebuild'] is True

            undo_response = client.post(f"/datasets/{derived_record['id']}/undo", json={'_csrf_token': token})
            undo_payload = undo_response.get_json()
            assert undo_response.status_code == 200
            assert undo_payload['dataset']['id'] == root_record['id']

            token = _set_session(client, dataset_id=derived_record['id'], filepath=str(transformed_source), token='rebuild-token')
            rebuild_response = client.post(f"/datasets/{derived_record['id']}/rebuild", json={'_csrf_token': token})
            rebuild_payload = rebuild_response.get_json()
            assert rebuild_response.status_code == 200
            assert rebuild_payload['dataset']['source_type'] == 'rebuilt'
            assert os.path.exists(rebuild_payload['dataset']['stored_path'])
    finally:
        app_module.app.config['UPLOAD_FOLDER'] = original_upload_folder
