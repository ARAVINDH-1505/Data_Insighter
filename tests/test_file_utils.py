import pandas as pd

from file_utils import read_data_file


def test_read_tsv_file(tmp_path):
    source = tmp_path / 'sales.tsv'
    source.write_text('region\trevenue\nNorth\t120\nSouth\t95\n', encoding='utf-8')

    df = read_data_file(str(source))

    assert df.to_dict(orient='records') == [
        {'region': 'North', 'revenue': 120},
        {'region': 'South', 'revenue': 95},
    ]


def test_read_jsonl_file(tmp_path):
    source = tmp_path / 'events.jsonl'
    source.write_text('{"user":"a","value":1}\n{"user":"b","value":2}\n', encoding='utf-8')

    df = read_data_file(str(source))

    assert df.to_dict(orient='records') == [
        {'user': 'a', 'value': 1},
        {'user': 'b', 'value': 2},
    ]


def test_read_excel_file(tmp_path):
    source = tmp_path / 'finance.xlsx'
    expected = pd.DataFrame([
        {'month': 'Jan', 'profit': 15.5},
        {'month': 'Feb', 'profit': 18.0},
    ])
    expected.to_excel(source, index=False)

    df = read_data_file(str(source))

    assert df.to_dict(orient='records') == expected.to_dict(orient='records')


def test_read_parquet_file(tmp_path):
    source = tmp_path / 'orders.parquet'
    expected = pd.DataFrame([
        {'order_id': 'A-1', 'amount': 42.0},
        {'order_id': 'A-2', 'amount': 57.5},
    ])
    expected.to_parquet(source, index=False)

    df = read_data_file(str(source))

    assert df.to_dict(orient='records') == expected.to_dict(orient='records')
