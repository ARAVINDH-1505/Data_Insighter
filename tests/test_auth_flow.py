import json

import app as app_module


def _set_csrf(client, token='test-token'):
    with client.session_transaction() as session_state:
        session_state['_csrf_token'] = token
    return token


def test_registration_blocks_duplicate_email(tmp_path, monkeypatch):
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({})
    app_module.app.config['TESTING'] = True

    with app_module.app.test_client() as client:
        token = _set_csrf(client)
        client.post('/register', data={
            'username': 'first_user',
            'email': 'shared@example.com',
            'password': 'Passw0rd!',
            'confirm_password': 'Passw0rd!',
            '_csrf_token': token,
        })

        token = _set_csrf(client, 'test-token-2')
        response = client.post('/register', data={
            'username': 'second_user',
            'email': 'shared@example.com',
            'password': 'Passw0rd!',
            'confirm_password': 'Passw0rd!',
            '_csrf_token': token,
        }, follow_redirects=True)

        stored_users = json.loads(users_file.read_text(encoding='utf-8'))
        assert list(stored_users.keys()) == ['first_user']
        assert b'Email is already registered' in response.data


def test_login_accepts_normalized_email(tmp_path, monkeypatch):
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'analyst': {
            'email': 'analyst@example.com',
            'password_hash': app_module.generate_password_hash('Passw0rd!'),
        }
    })
    app_module.app.config['TESTING'] = True

    with app_module.app.test_client() as client:
        token = _set_csrf(client)
        response = client.post('/login', data={
            'username': ' Analyst@Example.com ',
            'password': 'Passw0rd!',
            '_csrf_token': token,
        }, follow_redirects=False)

        assert response.status_code == 302
        assert response.headers['Location'].endswith('/')
