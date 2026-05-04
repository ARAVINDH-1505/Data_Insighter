import os

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
