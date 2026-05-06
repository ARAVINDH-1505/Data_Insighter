import json

import workspace_store


def test_atomic_write_preserves_existing_file_on_failure(tmp_path, monkeypatch):
    target = tmp_path / 'dataset.json'
    target.write_text('{"status": "original"}', encoding='utf-8')

    def failing_dump(payload, handle, indent=2):
        handle.write('{"status": ')
        raise TypeError('serialization failed')

    monkeypatch.setattr(workspace_store.json, 'dump', failing_dump)

    try:
        workspace_store._write_json_atomic(str(target), {'status': 'new'})
    except TypeError:
        pass

    assert json.loads(target.read_text(encoding='utf-8')) == {'status': 'original'}
    assert [path.name for path in tmp_path.iterdir()] == ['dataset.json']


def test_report_records_round_trip(tmp_path, monkeypatch):
    base_dir = tmp_path / 'workspace_data'
    monkeypatch.setattr(workspace_store, 'BASE_DIR', str(base_dir))
    monkeypatch.setattr(workspace_store, 'REPORTS_DIR', str(base_dir / 'reports'))
    workspace_store.ensure_workspace_dirs()

    record = workspace_store.create_report_record(
        'analyst',
        name='Weekly summary',
        dataset_id='ds_123',
        report_payload={'dataset_name': 'Sales', 'sections': [{'title': 'Overview', 'bullets': ['Rows: 10']}]},
    )

    fetched = workspace_store.get_report_record('analyst', record['id'])
    listed = workspace_store.list_report_records('analyst', dataset_id='ds_123')

    assert fetched['name'] == 'Weekly summary'
    assert fetched['report']['dataset_name'] == 'Sales'
    assert listed[0]['id'] == record['id']
