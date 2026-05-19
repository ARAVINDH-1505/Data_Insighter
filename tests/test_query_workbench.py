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


def _set_session(client, username, dataset_id, filepath, token='sql-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = username
        session_state['current_dataset_id'] = dataset_id
        session_state['current_filepath'] = filepath
    return token


def test_query_workbench_runs_grouped_sql_for_owner(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({'owner': {'email': 'owner@example.com', 'password_hash': 'x'}})
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\nNorth,50\n', encoding='utf-8')

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
        },
    )

    with app_module.app.test_client() as client:
        token = _set_session(client, 'owner', dataset_record['id'], str(source))
        response = client.post('/query_workbench', json={
            '_csrf_token': token,
            'sql': 'SELECT region, SUM(revenue) AS total_revenue FROM dataset GROUP BY 1 ORDER BY total_revenue DESC',
            'limit': 20,
        })
        payload = response.get_json()

    assert response.status_code == 200
    assert payload['result']['engine'] == 'duckdb_file_scan'
    assert payload['result']['rows'][0]['region'] == 'South'
    assert payload['result']['rows'][0]['total_revenue'] == 200


def test_query_workbench_honors_row_level_policies(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'owner': {'email': 'owner@example.com', 'password_hash': 'x'},
        'viewer': {'email': 'viewer@example.com', 'password_hash': 'x'},
    })
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\nNorth,50\n', encoding='utf-8')

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
        token = _set_session(client, 'viewer', dataset_record['id'], str(source))
        response = client.post('/query_workbench', json={
            '_csrf_token': token,
            'sql': 'SELECT SUM(revenue) AS total_revenue FROM dataset',
            'limit': 20,
        })
        payload = response.get_json()

    assert response.status_code == 200
    assert payload['result']['engine'] == 'duckdb_dataframe'
    assert payload['result']['rows'][0]['total_revenue'] == 150


def test_saved_query_library_round_trip(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({'owner': {'email': 'owner@example.com', 'password_hash': 'x'}})
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text('region,revenue\nNorth,100\nSouth,200\nNorth,50\n', encoding='utf-8')

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
        },
    )

    with app_module.app.test_client() as client:
        token = _set_session(client, 'owner', dataset_record['id'], str(source))
        save_response = client.post('/queries/save', json={
            '_csrf_token': token,
            'name': 'Revenue by region',
            'sql': 'SELECT region, SUM(revenue) AS total_revenue FROM dataset GROUP BY 1 ORDER BY total_revenue DESC',
        })
        save_payload = save_response.get_json()
        list_response = client.get('/query_library')
        fetch_response = client.get(f"/queries/{save_payload['query']['id']}")

    assert save_response.status_code == 200
    assert list_response.status_code == 200
    assert fetch_response.status_code == 200
    assert list_response.get_json()['queries'][0]['name'] == 'Revenue by region'
    assert 'SUM(revenue)' in fetch_response.get_json()['query']['sql']


def test_query_workbench_export_returns_csv(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({'owner': {'email': 'owner@example.com', 'password_hash': 'x'}})
    app_module.app.config['TESTING'] = True
    original_upload_folder = app_module.app.config['UPLOAD_FOLDER']
    original_managed_folder = app_module.app.config['MANAGED_DATASETS_FOLDER']
    app_module.app.config['UPLOAD_FOLDER'] = str(tmp_path / 'uploads')
    app_module.app.config['MANAGED_DATASETS_FOLDER'] = str(tmp_path / 'uploads' / 'managed')
    (tmp_path / 'uploads').mkdir(exist_ok=True)
    (tmp_path / 'uploads' / 'managed').mkdir(exist_ok=True)

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
        },
    )

    try:
        with app_module.app.test_client() as client:
            token = _set_session(client, 'owner', dataset_record['id'], str(source))
            response = client.post('/query_workbench/export', json={
                '_csrf_token': token,
                'name': 'revenue_extract',
                'sql': 'SELECT region, revenue FROM dataset ORDER BY revenue DESC',
                'limit': 100,
            })
        assert response.status_code == 200
        body = response.data.decode('utf-8')
        assert 'region,revenue' in body
        assert 'South,200' in body
        assert not any(path.is_file() for path in (tmp_path / 'uploads').iterdir())
    finally:
        app_module.app.config['UPLOAD_FOLDER'] = original_upload_folder
        app_module.app.config['MANAGED_DATASETS_FOLDER'] = original_managed_folder
