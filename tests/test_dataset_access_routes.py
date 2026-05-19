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


def _set_session(client, username, dataset_id, filepath, token='access-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = username
        session_state['current_dataset_id'] = dataset_id
        session_state['current_filepath'] = filepath
    return token


def test_shared_dataset_row_policy_filters_analysis_summary(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
        'viewer': {'email': 'viewer@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\nNorth,300\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
        'owner',
        source_name='sales.csv',
        stored_path=str(source),
        source_type='upload',
        row_count=3,
        column_count=2,
        metadata={
            'display_name': 'sales.csv',
            'columns': ['region', 'revenue'],
            'lineage_steps': [],
            'pipeline_steps': [],
            'shared_with': [{'user': 'viewer', 'role': 'viewer'}],
            'row_policies': [{'user': 'viewer', 'column': 'region', 'allowed_values': ['North']}],
        },
    )

    with app_module.app.test_client() as client:
        _set_session(client, 'viewer', dataset_record['id'], str(source))
        response = client.get('/analysis_summary')
        payload = response.get_json()

    assert response.status_code == 200
    assert payload['access_role'] == 'viewer'
    assert payload['summary']['dataset_overview']['rows'] == 2


def test_viewer_cannot_apply_transform_to_shared_dataset(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
        'viewer': {'email': 'viewer@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
        'owner',
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
            'shared_with': [{'user': 'viewer', 'role': 'viewer'}],
            'row_policies': [],
        },
    )

    with app_module.app.test_client() as client:
        token = _set_session(client, 'viewer', dataset_record['id'], str(source))
        response = client.post('/apply_transform', json={
            '_csrf_token': token,
            'operation': 'trim_text',
            'options': {},
        })
        payload = response.get_json()

    assert response.status_code == 403
    assert 'viewer access' in payload['error']


def test_dashboard_filter_options_returns_dimension_values_and_date_columns(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text(
        'region,observed_at,revenue\nNorth,2026-01-01,100\nSouth,2026-01-02,200\nNorth,2026-01-03,150\n',
        encoding='utf-8',
    )

    dataset_record = workspace_store.create_dataset_record(
        'owner',
        source_name='sales.csv',
        stored_path=str(source),
        source_type='upload',
        row_count=3,
        column_count=3,
        metadata={
            'display_name': 'sales.csv',
            'columns': ['region', 'observed_at', 'revenue'],
            'lineage_steps': [],
            'pipeline_steps': [],
            'shared_with': [],
            'row_policies': [],
        },
    )

    with app_module.app.test_client() as client:
        _set_session(client, 'owner', dataset_record['id'], str(source))
        response = client.get('/dashboard_filter_options')
        payload = response.get_json()

    assert response.status_code == 200
    assert payload['success'] is True
    assert payload['options']['region'] == ['North', 'South']
    assert 'observed_at' in payload['date_columns']


def test_owner_can_share_dataset_and_save_row_policy(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
        'analyst': {'email': 'analyst@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
        'owner',
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
            'shared_with': [],
            'row_policies': [],
        },
    )

    with app_module.app.test_client() as client:
        token = _set_session(client, 'owner', dataset_record['id'], str(source))
        share_response = client.post(f"/datasets/{dataset_record['id']}/share", json={
            '_csrf_token': token,
            'user': 'analyst',
            'role': 'editor',
        })
        policy_response = client.post(f"/datasets/{dataset_record['id']}/row_policy", json={
            '_csrf_token': token,
            'user': 'analyst',
            'column': 'region',
            'allowed_values': ['North'],
        })

    shared_payload = share_response.get_json()
    policy_payload = policy_response.get_json()
    assert share_response.status_code == 200
    assert policy_response.status_code == 200
    assert shared_payload['dataset']['metadata']['shared_with'][0]['user'] == 'analyst'
    assert policy_payload['dataset']['metadata']['row_policies'][0]['column'] == 'region'


def test_shared_dataset_exposes_saved_reports_and_dashboards_to_collaborators(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
        'viewer': {'email': 'viewer@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
        'owner',
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
            'shared_with': [{'user': 'viewer', 'role': 'viewer'}],
            'row_policies': [],
        },
    )

    with app_module.app.test_client() as owner_client:
        token = _set_session(owner_client, 'owner', dataset_record['id'], str(source))
        report_response = owner_client.post('/reports/save', json={
            '_csrf_token': token,
            'name': 'Owner snapshot',
        })
        dashboard_response = owner_client.post('/dashboards/save', json={
            '_csrf_token': token,
            'name': 'Owner dashboard',
            'dashboard_viz': [{'id': 1, 'type': 'bar', 'columns': ['region']}],
            'dashboard_state': {'globalFilter': {}},
        })

    report_payload = report_response.get_json()
    dashboard_payload = dashboard_response.get_json()
    assert report_response.status_code == 200
    assert dashboard_response.status_code == 200

    with app_module.app.test_client() as viewer_client:
        _set_session(viewer_client, 'viewer', dataset_record['id'], str(source))
        report_library_response = viewer_client.get('/report_library')
        dashboard_library_response = viewer_client.get('/dashboard_library')
        report_fetch_response = viewer_client.get(f"/reports/{report_payload['report']['id']}")
        dashboard_fetch_response = viewer_client.get(f"/dashboards/{dashboard_payload['dashboard']['id']}")

    report_library_payload = report_library_response.get_json()
    dashboard_library_payload = dashboard_library_response.get_json()
    assert report_library_response.status_code == 200
    assert dashboard_library_response.status_code == 200
    assert report_fetch_response.status_code == 200
    assert dashboard_fetch_response.status_code == 200
    assert report_library_payload['reports'][0]['access_role'] == 'viewer'
    assert dashboard_library_payload['dashboards'][0]['access_role'] == 'viewer'


def test_owner_can_explicitly_share_dashboard_and_report_outside_dataset_access(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
        'analyst': {'email': 'analyst@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
        'owner',
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
            'shared_with': [],
            'row_policies': [],
        },
    )

    with app_module.app.test_client() as owner_client:
        token = _set_session(owner_client, 'owner', dataset_record['id'], str(source))
        report_response = owner_client.post('/reports/save', json={
            '_csrf_token': token,
            'name': 'Board memo',
        })
        dashboard_response = owner_client.post('/dashboards/save', json={
            '_csrf_token': token,
            'name': 'Board dashboard',
            'dashboard_viz': [{'id': 2, 'type': 'line', 'columns': ['region', 'revenue']}],
            'dashboard_state': {'globalFilter': {}},
        })
        report_id = report_response.get_json()['report']['id']
        dashboard_id = dashboard_response.get_json()['dashboard']['id']

        shared_report_response = owner_client.post(f'/reports/{report_id}/share', json={
            '_csrf_token': token,
            'user': 'analyst',
            'role': 'viewer',
        })
        shared_dashboard_response = owner_client.post(f'/dashboards/{dashboard_id}/share', json={
            '_csrf_token': token,
            'user': 'analyst',
            'role': 'editor',
        })

    assert shared_report_response.status_code == 200
    assert shared_dashboard_response.status_code == 200

    with app_module.app.test_client() as analyst_client:
        _set_session(analyst_client, 'analyst', dataset_record['id'], str(source))
        report_fetch_response = analyst_client.get(f'/reports/{report_id}')
        dashboard_fetch_response = analyst_client.get(f'/dashboards/{dashboard_id}')

    assert report_fetch_response.status_code == 200
    assert dashboard_fetch_response.status_code == 200
