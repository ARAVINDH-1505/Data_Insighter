import app as app_module


def test_user_save_persists_to_sqlite_auth_store(tmp_path, monkeypatch):
    users_file = tmp_path / 'users.json'
    monkeypatch.setattr(app_module, 'USERS_FILE', str(users_file))
    app_module._save_users({
        'existing': {'email': 'a@example.com', 'password_hash': 'hash-a'},
        'new_user': {'email': 'b@example.com', 'password_hash': 'hash-b'},
    })

    stored = app_module._load_users()

    assert stored['existing']['email'] == 'a@example.com'
    assert stored['new_user']['password_hash'] == 'hash-b'
    assert (tmp_path / 'auth.db').exists()
