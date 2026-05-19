import os
import pandas as pd

import app as app_module


def _set_csrf(client, token='test-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
        session_state['user'] = 'analyst'
    return token


def test_export_visualization_uses_upload_folder_and_cleans_up(tmp_path, monkeypatch):
    export_calls = []

    def fake_export(self, viz_type, viz_data, output_dir):
        export_calls.append((viz_type, output_dir))
        output_path = tmp_path / 'export_test.png'
        output_path.write_bytes(b'png-bytes')
        return str(output_path)

    monkeypatch.setattr(
        app_module.VisualizationGenerator,
        'export_visualization',
        fake_export,
    )
    app_module.app.config['TESTING'] = True
    app_module.app.config['UPLOAD_FOLDER'] = str(tmp_path)

    with app_module.app.test_client() as client:
        token = _set_csrf(client)
        response = client.post('/export', json={
            '_csrf_token': token,
            'type': 'png',
            'data': {'data': [], 'layout': {}},
        })

    assert response.status_code == 200
    assert response.data == b'png-bytes'
    assert export_calls == [('png', str(tmp_path))]
    assert not os.path.exists(tmp_path / 'export_test.png')


def test_export_dashboard_streams_html_without_writing_temp_files(tmp_path):
    app_module.app.config['TESTING'] = True
    app_module.app.config['UPLOAD_FOLDER'] = str(tmp_path)

    with app_module.app.test_client() as client:
        token = _set_csrf(client)
        response = client.post('/export_dashboard', json={
            '_csrf_token': token,
            'dashboard_data': {
                'dashboard_viz': [{'id': 1, 'type': 'bar', 'title': 'Revenue'}],
                'dashboard_state': {'pages': [{'id': 'page_overview', 'name': 'Overview'}]},
            },
        })

    assert response.status_code == 200
    assert b'Revenue' in response.data
    assert list(tmp_path.iterdir()) == []


def test_export_report_uses_dataset_processor_and_keeps_upload_folder_clean(tmp_path, monkeypatch):
    app_module.app.config['TESTING'] = True
    app_module.app.config['UPLOAD_FOLDER'] = str(tmp_path)

    dataset_record = {
        'id': 'ds_export',
        'source_name': 'sales.csv',
        'metadata': {'display_name': 'Sales snapshot'},
    }
    frame = pd.DataFrame({'region': ['North'], 'revenue': [120]})

    def fake_load_active_dataset_frame():
        return frame, dataset_record, str(tmp_path / 'sales.csv')

    class FakeProcessor:
        def get_analysis_summary(self):
            return {
                'dataset_overview': {
                    'rows': 1,
                    'columns': 2,
                    'completeness_pct': 100,
                    'duplicate_rows': 0,
                    'duplicate_pct': 0,
                },
                'executive_takeaways': ['Certified measure: Average Revenue'],
                'key_insights': [],
                'quality_alerts': [],
            }

    def fake_processor_for_dataset(export_frame, export_dataset_record):
        assert export_frame.equals(frame)
        assert export_dataset_record is dataset_record
        return FakeProcessor()

    def fail_if_dataprocessor_used(*args, **kwargs):
        raise AssertionError('export_report should use processor_for_dataset')

    monkeypatch.setattr(app_module, 'load_active_dataset_frame', fake_load_active_dataset_frame)
    monkeypatch.setattr(app_module, 'processor_for_dataset', fake_processor_for_dataset)
    monkeypatch.setattr(app_module, 'DataProcessor', fail_if_dataprocessor_used)

    with app_module.app.test_client() as client:
        token = _set_csrf(client)
        with client.session_transaction() as session_state:
            session_state['current_dataset_id'] = dataset_record['id']
            session_state['current_filepath'] = str(tmp_path / 'sales.csv')
        response = client.post('/export_report', json={
            '_csrf_token': token,
            'type': 'html',
        })

    assert response.status_code == 200
    assert b'Certified measure: Average Revenue' in response.data
    assert list(tmp_path.iterdir()) == []
