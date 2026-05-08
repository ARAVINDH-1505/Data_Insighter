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


def _set_session(client, username, dataset_id, filepath, token='job-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = username
        session_state['current_dataset_id'] = dataset_id
        session_state['current_filepath'] = filepath
    return token


def test_incremental_refresh_job_materializes_and_appends_new_rows(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True
    original_upload_folder = app_module.app.config['UPLOAD_FOLDER']
    original_managed_folder = app_module.app.config['MANAGED_DATASETS_FOLDER']
    app_module.app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    app_module.app.config['MANAGED_DATASETS_FOLDER'] = str(tmp_path / 'uploads' / 'managed')
    os.makedirs(app_module.app.config['MANAGED_DATASETS_FOLDER'], exist_ok=True)

    source = tmp_path / 'orders.csv'
    source.write_text('order_id,revenue\n1,100\n2,200\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
        'owner',
        source_name='orders.csv',
        stored_path=str(source),
        source_type='upload',
        row_count=2,
        column_count=2,
        metadata={
            'display_name': 'orders.csv',
            'columns': ['order_id', 'revenue'],
            'lineage_steps': [],
            'pipeline_steps': [],
            'schema_snapshot': {
                'columns': ['order_id', 'revenue'],
                'dtypes': {'order_id': 'int64', 'revenue': 'int64'},
            },
        },
    )

    try:
        with app_module.app.test_client() as client:
            token = _set_session(client, 'owner', dataset_record['id'], str(source))
            create_response = client.post(f"/datasets/{dataset_record['id']}/refresh_jobs", json={
                '_csrf_token': token,
                'cadence_minutes': 60,
                'mode': 'incremental',
                'incremental_column': 'order_id',
            })
            create_payload = create_response.get_json()
            assert create_response.status_code == 200
            assert create_payload['dataset']['stored_path'].endswith('.parquet')
            assert create_payload['job']['mode'] == 'incremental'

            source.write_text('order_id,revenue\n1,100\n2,200\n3,250\n', encoding='utf-8')

            run_response = client.post(f"/refresh_jobs/{create_payload['job']['id']}/run", json={
                '_csrf_token': token,
            })
            run_payload = run_response.get_json()
            assert run_response.status_code == 200
            assert run_payload['dataset']['row_count'] == 3
            assert run_payload['refresh_details']['mode'] == 'incremental'
            assert run_payload['refresh_details']['rows_added'] == 1

            list_response = client.get(f"/datasets/{dataset_record['id']}/refresh_jobs")
            list_payload = list_response.get_json()
            assert list_response.status_code == 200
            assert list_payload['jobs'][0]['last_status'] == 'succeeded'
    finally:
        app_module.app.config['UPLOAD_FOLDER'] = original_upload_folder
        app_module.app.config['MANAGED_DATASETS_FOLDER'] = original_managed_folder
