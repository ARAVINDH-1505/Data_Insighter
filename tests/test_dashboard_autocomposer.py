import app as app_module
import workspace_store
from dashboard_autocomposer import compose_starter_dashboard


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


def _set_session(client, dataset_id, filepath, token='dash-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = 'analyst'
        session_state['current_dataset_id'] = dataset_id
        session_state['current_filepath'] = filepath
    return token


def test_compose_starter_dashboard_returns_story_blocks():
    summary = {
        'semantic_model': {
            'dataset_role': 'fact',
            'row_grain': 'order_id',
            'primary_date_column': 'order_date',
            'recommended_measures': ['revenue', 'profit'],
            'recommended_dimensions': ['region', 'category'],
        },
        'recommended_visualizations': [
            {'title': 'Revenue by region', 'type': 'bar', 'columns': ['region', 'revenue'], 'sample_percentage': 100},
            {'title': 'Revenue trend', 'type': 'line', 'columns': ['order_date', 'revenue'], 'sample_percentage': 100},
        ],
        'key_insights': [
            {
                'kind': 'segment_driver',
                'recommended_chart': {'title': 'Revenue by region', 'type': 'bar', 'columns': ['region', 'revenue'], 'sample_percentage': 100},
            },
            {
                'kind': 'anomaly',
                'recommended_chart': {'title': 'Revenue anomaly scan', 'type': 'box', 'columns': ['revenue'], 'sample_percentage': 100},
            },
        ],
        'executive_takeaways': ['Revenue is climbing.', 'North leads contribution.'],
    }

    composed = compose_starter_dashboard(summary, dataset_name='sales.csv')

    assert composed['layout_name'] == 'executive_storyboard'
    assert any(block['type'] == 'text' for block in composed['dashboard_viz'])
    assert any(block['type'] == 'line' for block in composed['dashboard_viz'])
    assert any(block['type'] == 'bar' for block in composed['dashboard_viz'])


def test_starter_dashboard_route_returns_storyboard(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({'analyst': {'email': 'analyst@example.com', 'password_hash': 'x'}})
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'sales.csv'
    source.write_text(
        'order_id,order_date,region,revenue,profit\n'
        '1,2026-01-01,North,100,25\n'
        '2,2026-01-02,South,80,20\n'
        '3,2026-01-03,North,120,35\n'
        '4,2026-01-04,East,70,12\n'
        '5,2026-01-05,North,150,42\n'
        '6,2026-01-06,South,90,18\n'
        '7,2026-01-07,East,75,15\n'
        '8,2026-01-08,North,160,48\n'
        '9,2026-01-09,South,92,19\n'
        '10,2026-01-10,North,170,50\n'
        '11,2026-01-11,East,82,16\n'
        '12,2026-01-12,South,95,20\n',
        encoding='utf-8',
    )

    dataset_record = workspace_store.create_dataset_record(
        'analyst',
        source_name='sales.csv',
        stored_path=str(source),
        source_type='upload',
        row_count=12,
        column_count=5,
        metadata={
            'display_name': 'sales.csv',
            'columns': ['order_id', 'order_date', 'region', 'revenue', 'profit'],
            'lineage_steps': [],
            'pipeline_steps': [],
        },
    )

    with app_module.app.test_client() as client:
        token = _set_session(client, dataset_record['id'], str(source))
        response = client.post('/starter_dashboard', json={'_csrf_token': token})
        payload = response.get_json()

    assert response.status_code == 200
    assert payload['layout_name'] == 'executive_storyboard'
    assert any(block['type'] == 'text' for block in payload['dashboard_viz'])
    assert any(block['type'] == 'kpi' for block in payload['dashboard_viz'])
