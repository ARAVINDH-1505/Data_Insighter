import workspace_store
from refresh_job_service import (
    create_refresh_schedule,
    refresh_worker_summary,
    run_due_refresh_jobs,
)


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


def test_due_refresh_jobs_capture_worker_runtime_metadata(tmp_path, monkeypatch):
    _configure_workspace_dirs(tmp_path, monkeypatch)
    managed_dir = tmp_path / 'managed'
    managed_dir.mkdir(parents=True, exist_ok=True)

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

    updated_record, job = create_refresh_schedule(
        'owner',
        dataset_record,
        str(managed_dir),
        cadence_minutes=60,
        mode='full',
    )

    results = run_due_refresh_jobs(str(managed_dir), runner_id='worker:test', max_jobs=1)
    assert results
    assert results[0]['success'] is True

    refreshed_job = workspace_store.get_refresh_job_record('owner', job['id'])
    runtime = refreshed_job['metadata']['worker_runtime']
    summary = refresh_worker_summary(updated_record['id'])

    assert runtime['last_worker_id'] == 'worker:test'
    assert runtime['claimed_by'] is None
    assert refreshed_job['last_status'] == 'succeeded'
    assert summary['active_claims'] == 0
    assert summary['worker_ids'] == ['worker:test']
