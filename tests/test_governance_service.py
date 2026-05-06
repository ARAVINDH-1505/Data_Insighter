import app as app_module
import workspace_store
from governance_service import build_governance_summary, infer_sensitivity_label


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


def _set_session(client, dataset_id, filepath, token='test-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = 'analyst'
        session_state['current_dataset_id'] = dataset_id
        session_state['current_filepath'] = filepath


def test_infer_sensitivity_label_detects_confidential_fields():
    summary = {
        'semantic_profiles': [
            {'name': 'customer_email'},
            {'name': 'revenue'},
        ]
    }

    result = infer_sensitivity_label(summary)

    assert result['label'] == 'Confidential'


def test_governance_summary_route_returns_activity_and_label(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    app_module.app.config['TESTING'] = True

    source = tmp_path / 'customers.csv'
    source.write_text('customer_email,revenue\nalice@example.com,120\nbob@example.com,90\n', encoding='utf-8')

    dataset_record = workspace_store.create_dataset_record(
        'analyst',
        source_name='customers.csv',
        stored_path=str(source),
        source_type='upload',
        row_count=2,
        column_count=2,
        metadata={
            'display_name': 'customers.csv',
            'columns': ['customer_email', 'revenue'],
            'lineage_steps': [],
            'pipeline_steps': [],
        },
    )
    workspace_store.log_audit_event(
        'analyst',
        action='dataset_uploaded',
        dataset_id=dataset_record['id'],
        artifact_id=dataset_record['id'],
        details={'display_name': 'customers.csv'},
    )

    with app_module.app.test_client() as client:
        _set_session(client, dataset_record['id'], str(source))
        response = client.get('/governance_summary')
        payload = response.get_json()

    assert response.status_code == 200
    assert payload['governance']['sensitivity']['label'] == 'Confidential'
    assert payload['governance']['activity'][0]['action'] == 'dataset_uploaded'
    assert payload['governance']['pipeline']['can_rebuild'] is True


def test_build_governance_summary_counts_downstream_assets():
    dataset_record = {
        'id': 'ds_1',
        'source_type': 'upload',
        'metadata': {'pipeline_steps': [], 'lineage_steps': []},
    }
    analysis_summary = {
        'semantic_profiles': [{'name': 'region'}],
        'quality_alerts': [{'severity': 'success'}],
    }

    summary = build_governance_summary(
        dataset_record,
        analysis_summary,
        audit_events=[{'action': 'dataset_uploaded'}],
        dashboards=[{'id': 'db_1'}, {'id': 'db_2'}],
        measures=[{'id': 'm_1'}],
        reports=[{'id': 'r_1'}, {'id': 'r_2'}, {'id': 'r_3'}],
    )

    assert summary['downstream_assets'] == {'dashboards': 2, 'measures': 1, 'reports': 3}
