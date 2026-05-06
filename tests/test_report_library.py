import os

import app as app_module
import workspace_store


def _configure_workspace_dirs(tmp_path, monkeypatch):
    base_dir = tmp_path / 'workspace_data'
    monkeypatch.setattr(workspace_store, 'BASE_DIR', str(base_dir))
    monkeypatch.setattr(workspace_store, 'DATASETS_DIR', str(base_dir / 'datasets'))
    monkeypatch.setattr(workspace_store, 'DASHBOARDS_DIR', str(base_dir / 'dashboards'))
    monkeypatch.setattr(workspace_store, 'REPORTS_DIR', str(base_dir / 'reports'))
    monkeypatch.setattr(workspace_store, 'RELATIONSHIPS_DIR', str(base_dir / 'relationships'))
    monkeypatch.setattr(workspace_store, 'MEASURES_DIR', str(base_dir / 'measures'))
    monkeypatch.setattr(workspace_store, 'AUDIT_DIR', str(base_dir / 'audit'))
    workspace_store.ensure_workspace_dirs()


def _set_session(client, dataset_id, filepath, token='report-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = 'analyst'
        session_state['current_dataset_id'] = dataset_id
        session_state['current_filepath'] = filepath
    return token


def test_report_snapshot_routes_round_trip(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    app_module.app.config['TESTING'] = True
    original_upload_folder = app_module.app.config['UPLOAD_FOLDER']
    app_module.app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    os.makedirs(app_module.app.config['UPLOAD_FOLDER'], exist_ok=True)

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
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

    try:
        with app_module.app.test_client() as client:
            token = _set_session(client, dataset_record['id'], str(source))

            save_response = client.post('/reports/save', json={
                '_csrf_token': token,
                'name': 'QBR summary',
            })
            save_payload = save_response.get_json()
            assert save_response.status_code == 200
            assert save_payload['report']['name'] == 'QBR summary'

            list_response = client.get('/report_library')
            list_payload = list_response.get_json()
            assert list_response.status_code == 200
            assert len(list_payload['reports']) == 1
            assert list_payload['reports'][0]['section_count'] >= 1

            report_id = save_payload['report']['id']
            fetch_response = client.get(f'/reports/{report_id}')
            fetch_payload = fetch_response.get_json()
            assert fetch_response.status_code == 200
            assert fetch_payload['report']['report']['dataset_name'] == 'sales.csv'
    finally:
        app_module.app.config['UPLOAD_FOLDER'] = original_upload_folder
