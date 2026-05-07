import json
import sqlite3
from pathlib import Path
from typing import Iterable, Optional

import chardet
import pandas as pd


DELIMITED_EXTENSIONS = {'csv', 'tsv'}
JSON_EXTENSIONS = {'json', 'jsonl', 'ndjson'}
EXCEL_EXTENSIONS = {'xlsx', 'xls'}
PARQUET_EXTENSIONS = {'parquet'}
SQLITE_EXTENSIONS = {'db', 'sqlite', 'sqlite3'}
SUPPORTED_EXTENSIONS = DELIMITED_EXTENSIONS | JSON_EXTENSIONS | EXCEL_EXTENSIONS | PARQUET_EXTENSIONS | SQLITE_EXTENSIONS


def _detect_encoding(filepath: str) -> str:
    with open(filepath, 'rb') as handle:
        raw_data = handle.read()
    result = chardet.detect(raw_data)
    return result.get('encoding') or 'utf-8'


def _try_read_csv(filepath: str, encodings: Iterable[str], separators: Iterable[Optional[str]]) -> pd.DataFrame:
    last_error = None
    for encoding in encodings:
        for separator in separators:
            try:
                kwargs = {
                    'filepath_or_buffer': filepath,
                    'encoding': encoding,
                    'engine': 'python',
                    'on_bad_lines': 'skip',
                    'quotechar': '"',
                    'skipinitialspace': True,
                    'thousands': ',',
                    'decimal': '.',
                }
                if separator is None:
                    kwargs['sep'] = None
                else:
                    kwargs['sep'] = separator

                df = pd.read_csv(**kwargs)
                if not df.empty:
                    return df
            except Exception as exc:
                last_error = exc
                continue
    raise ValueError(f'Could not read delimited file with supported encodings or separators: {last_error}')


def _read_delimited_file(filepath: str, extension: str) -> pd.DataFrame:
    detected_encoding = _detect_encoding(filepath)
    encodings = [detected_encoding, 'utf-8', 'latin1', 'iso-8859-1', 'cp1252', 'utf-16', 'utf-32']
    separators = ['\t'] if extension == 'tsv' else [None, ',', ';', '\t', '|']

    try:
        return _try_read_csv(filepath, encodings, separators)
    except ValueError:
        if extension == 'csv':
            try:
                df = pd.read_csv(filepath, on_bad_lines='skip', thousands=',', decimal='.')
                if not df.empty:
                    return df
            except Exception:
                pass
        raise


def _normalize_json_payload(payload) -> pd.DataFrame:
    if isinstance(payload, list):
        return pd.json_normalize(payload)
    if isinstance(payload, dict):
        return pd.json_normalize([payload])
    raise ValueError('Unsupported JSON structure')


def _read_json_file(filepath: str, extension: str) -> pd.DataFrame:
    detected_encoding = _detect_encoding(filepath)
    encodings = [detected_encoding, 'utf-8', 'latin1', 'iso-8859-1', 'cp1252', 'utf-16', 'utf-32']

    if extension in {'jsonl', 'ndjson'}:
        try:
            df = pd.read_json(filepath, lines=True)
            if not df.empty:
                return df
        except Exception as exc:
            raise ValueError(f'Could not read newline-delimited JSON: {exc}') from exc

    for encoding in encodings:
        try:
            with open(filepath, 'r', encoding=encoding) as handle:
                payload = json.load(handle)
            df = _normalize_json_payload(payload)
            if not df.empty:
                return df
        except Exception:
            continue

    try:
        df = pd.read_json(filepath, lines=True)
        if not df.empty:
            return df
    except Exception:
        pass

    raise ValueError('Could not read JSON file with any supported encoding')


def _read_excel_file(filepath: str) -> pd.DataFrame:
    workbook = pd.ExcelFile(filepath)
    for sheet_name in workbook.sheet_names:
        df = workbook.parse(sheet_name=sheet_name)
        if not df.empty:
            return df
    raise ValueError('The workbook does not contain a non-empty sheet')


def _read_parquet_file(filepath: str) -> pd.DataFrame:
    return pd.read_parquet(filepath)


def _read_sqlite_file(filepath: str, source_table: Optional[str] = None) -> pd.DataFrame:
    connection = sqlite3.connect(filepath)
    try:
        tables = pd.read_sql_query(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name",
            connection,
        )['name'].tolist()
        if not tables:
            raise ValueError('The SQLite database does not contain any user tables')

        candidate_tables = [source_table] if source_table else tables
        for table_name in candidate_tables:
            if not table_name:
                continue
            safe_table = str(table_name).replace('"', '""')
            df = pd.read_sql_query(f'SELECT * FROM "{safe_table}"', connection)
            if not df.empty:
                df.attrs['source_table'] = table_name
                df.attrs['available_tables'] = tables
                return df

        raise ValueError('The SQLite database tables are empty or the selected table could not be read')
    finally:
        connection.close()


def read_data_file(filepath: str, source_table: Optional[str] = None) -> pd.DataFrame:
    """Read a supported analytics file and return a pandas DataFrame."""
    extension = Path(filepath).suffix.lower().lstrip('.')

    try:
        if extension in DELIMITED_EXTENSIONS:
            return _read_delimited_file(filepath, extension)
        if extension in JSON_EXTENSIONS:
            return _read_json_file(filepath, extension)
        if extension in EXCEL_EXTENSIONS:
            return _read_excel_file(filepath)
        if extension in PARQUET_EXTENSIONS:
            return _read_parquet_file(filepath)
        if extension in SQLITE_EXTENSIONS:
            return _read_sqlite_file(filepath, source_table=source_table)
        raise ValueError(f'Unsupported file format: {extension}')
    except Exception as exc:
        raise ValueError(f'Error reading file: {exc}') from exc
