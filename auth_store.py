import os
import sqlite3
from datetime import datetime
from typing import Dict, Optional

import workspace_store


def _default_db_path() -> str:
    return os.path.join(workspace_store.BASE_DIR, 'auth.db')


def _normalize_email(email: str) -> str:
    return (email or '').strip().lower()


def _connect(db_path: Optional[str] = None) -> sqlite3.Connection:
    target = db_path or _default_db_path()
    os.makedirs(os.path.dirname(target), exist_ok=True)
    connection = sqlite3.connect(target)
    connection.row_factory = sqlite3.Row
    return connection


def ensure_user_store(db_path: Optional[str] = None) -> None:
    with _connect(db_path) as connection:
        connection.execute(
            '''
            CREATE TABLE IF NOT EXISTS users (
                username TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            '''
        )
        connection.execute(
            'CREATE INDEX IF NOT EXISTS idx_users_email ON users(email)'
        )
        connection.commit()


def list_users(db_path: Optional[str] = None) -> Dict[str, Dict[str, str]]:
    ensure_user_store(db_path)
    with _connect(db_path) as connection:
        rows = connection.execute(
            'SELECT username, email, password_hash, created_at, updated_at FROM users ORDER BY username'
        ).fetchall()

    return {
        row['username']: {
            'email': row['email'],
            'password_hash': row['password_hash'],
            'created_at': row['created_at'],
            'updated_at': row['updated_at'],
        }
        for row in rows
    }


def get_user(username: str, db_path: Optional[str] = None) -> Optional[Dict[str, str]]:
    ensure_user_store(db_path)
    with _connect(db_path) as connection:
        row = connection.execute(
            'SELECT username, email, password_hash, created_at, updated_at FROM users WHERE username = ?',
            (username,),
        ).fetchone()
    if not row:
        return None
    return {
        'username': row['username'],
        'email': row['email'],
        'password_hash': row['password_hash'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def get_user_by_email(email: str, db_path: Optional[str] = None) -> Optional[Dict[str, str]]:
    ensure_user_store(db_path)
    normalized = _normalize_email(email)
    with _connect(db_path) as connection:
        row = connection.execute(
            'SELECT username, email, password_hash, created_at, updated_at FROM users WHERE email = ?',
            (normalized,),
        ).fetchone()
    if not row:
        return None
    return {
        'username': row['username'],
        'email': row['email'],
        'password_hash': row['password_hash'],
        'created_at': row['created_at'],
        'updated_at': row['updated_at'],
    }


def email_exists(email: str, exclude_username: Optional[str] = None, db_path: Optional[str] = None) -> bool:
    normalized = _normalize_email(email)
    ensure_user_store(db_path)
    with _connect(db_path) as connection:
        if exclude_username:
            row = connection.execute(
                'SELECT 1 FROM users WHERE email = ? AND username != ? LIMIT 1',
                (normalized, exclude_username),
            ).fetchone()
        else:
            row = connection.execute(
                'SELECT 1 FROM users WHERE email = ? LIMIT 1',
                (normalized,),
            ).fetchone()
    return bool(row)


def upsert_user(
    username: str,
    email: str,
    password_hash: str,
    *,
    created_at: Optional[str] = None,
    updated_at: Optional[str] = None,
    db_path: Optional[str] = None,
) -> Dict[str, str]:
    ensure_user_store(db_path)
    now = datetime.utcnow().isoformat() + 'Z'
    created_value = created_at or now
    updated_value = updated_at or now
    normalized_email = _normalize_email(email)

    with _connect(db_path) as connection:
        connection.execute(
            '''
            INSERT INTO users(username, email, password_hash, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(username) DO UPDATE SET
                email = excluded.email,
                password_hash = excluded.password_hash,
                updated_at = excluded.updated_at
            ''',
            (username, normalized_email, password_hash, created_value, updated_value),
        )
        connection.commit()

    return {
        'username': username,
        'email': normalized_email,
        'password_hash': password_hash,
        'created_at': created_value,
        'updated_at': updated_value,
    }


def replace_users(users: Dict[str, Dict[str, str]], db_path: Optional[str] = None) -> None:
    ensure_user_store(db_path)
    normalized_payload = {
        username: {
            'email': _normalize_email((details or {}).get('email', '')),
            'password_hash': (details or {}).get('password_hash', ''),
            'created_at': (details or {}).get('created_at'),
            'updated_at': (details or {}).get('updated_at'),
        }
        for username, details in (users or {}).items()
    }

    with _connect(db_path) as connection:
        connection.execute('DELETE FROM users')
        for username, details in normalized_payload.items():
            now = datetime.utcnow().isoformat() + 'Z'
            connection.execute(
                '''
                INSERT INTO users(username, email, password_hash, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?)
                ''',
                (
                    username,
                    details['email'],
                    details['password_hash'],
                    details['created_at'] or now,
                    details['updated_at'] or now,
                ),
            )
        connection.commit()


def migrate_legacy_users(legacy_path: str, db_path: Optional[str] = None) -> None:
    if not legacy_path or not os.path.exists(legacy_path):
        ensure_user_store(db_path)
        return

    existing = list_users(db_path)
    if existing:
        return

    import json

    try:
        with open(legacy_path, 'r', encoding='utf-8') as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError):
        payload = {}

    replace_users(payload, db_path)
