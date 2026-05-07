from __future__ import annotations

import os
import warnings
from functools import lru_cache
from typing import Any, Dict, Optional

import pandas as pd

from access_control_service import apply_row_policies
from file_utils import read_data_file


def _dataset_signature(filepath: str, source_table: str | None = None) -> tuple[str, int, int, str]:
    stat = os.stat(filepath)
    return os.path.abspath(filepath), int(stat.st_mtime_ns), int(stat.st_size), source_table or ''


def prepare_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    prepared = df.copy()
    prepared.columns = [str(column).strip() for column in prepared.columns]
    prepared = prepared.dropna(axis=1, how='all').dropna(how='all').reset_index(drop=True)

    for column in prepared.columns:
        series = prepared[column]

        if pd.api.types.is_object_dtype(series) or pd.api.types.is_string_dtype(series):
            non_null = series.dropna().astype(str).str.strip()
            if non_null.empty:
                continue

            numeric_series = pd.to_numeric(non_null.str.replace(",", "", regex=False), errors='coerce')
            if numeric_series.notna().mean() >= 0.8:
                prepared[column] = pd.to_numeric(
                    series.astype(str).str.replace(",", "", regex=False),
                    errors='coerce'
                )
                continue

            with warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                datetime_series = pd.to_datetime(series, errors='coerce')
            if datetime_series.notna().mean() >= 0.8:
                prepared[column] = datetime_series

    return prepared


@lru_cache(maxsize=24)
def _cached_prepared_dataframe(signature: tuple[str, int, int, str]) -> pd.DataFrame:
    filepath, _, _, source_table = signature
    return prepare_dataframe(read_data_file(filepath, source_table=source_table or None))


def load_prepared_dataframe(filepath: str, source_table: str | None = None) -> pd.DataFrame:
    return _cached_prepared_dataframe(_dataset_signature(filepath, source_table=source_table)).copy(deep=True)


def load_accessible_dataframe(
    filepath: str,
    dataset_record: Optional[Dict[str, Any]] = None,
    username: Optional[str] = None,
) -> pd.DataFrame:
    source_table = (dataset_record or {}).get('metadata', {}).get('source_table')
    prepared = load_prepared_dataframe(filepath, source_table=source_table)
    if dataset_record and username:
        return apply_row_policies(prepared, dataset_record, username)
    return prepared


def clear_runtime_cache() -> None:
    _cached_prepared_dataframe.cache_clear()
