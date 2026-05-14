import pandas as pd

from semantic_model import infer_dataset_semantics, summarize_semantic_model


def test_semantic_profiles_capture_aggregation_and_format_hints():
    df = pd.DataFrame({
        'order_id': [101, 102, 103, 104],
        'order_date': pd.to_datetime(['2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04']),
        'region': ['North', 'South', 'North', 'East'],
        'revenue': [120.0, 140.0, 90.0, 200.0],
        'conversion_rate': [0.12, 0.11, 0.15, 0.10],
    })

    profiles = {profile['name']: profile for profile in infer_dataset_semantics(df)}

    assert profiles['order_id']['semantic_role'] == 'identifier'
    assert profiles['revenue']['default_aggregation'] == 'sum'
    assert profiles['revenue']['format_hint'] == 'currency'
    assert profiles['revenue']['is_additive'] is True
    assert profiles['conversion_rate']['default_aggregation'] == 'average'
    assert profiles['conversion_rate']['is_additive'] is False
    assert profiles['order_date']['time_grain'] == 'daily'


def test_semantic_model_summary_infers_fact_like_dataset_grain():
    df = pd.DataFrame({
        'order_id': [1001, 1002, 1003, 1004],
        'order_date': pd.to_datetime(['2026-02-01', '2026-02-02', '2026-02-03', '2026-02-04']),
        'region': ['North', 'South', 'East', 'West'],
        'sales': [220, 140, 310, 175],
        'profit': [32, 18, 55, 21],
    })

    profiles = infer_dataset_semantics(df)
    model = summarize_semantic_model(df, profiles)

    assert model['dataset_role'] == 'fact'
    assert 'order_id' in model['grain_candidates']
    assert model['primary_date_column'] == 'order_date'
    assert 'sales' in model['recommended_measures']
    assert model['semantic_coverage']['measures'] == 2


def test_semantic_overrides_promote_certified_business_definition():
    df = pd.DataFrame({
        'revenue': [120.0, 140.0, 90.0, 200.0],
        'region': ['North', 'South', 'North', 'East'],
    })

    profiles = infer_dataset_semantics(df, overrides={
        'revenue': {
            'business_name': 'Net Revenue',
            'default_aggregation': 'average',
            'certified': True,
            'description': 'Use average deal value for this dataset.',
        }
    })
    profile_map = {profile['name']: profile for profile in profiles}
    model = summarize_semantic_model(df, profiles)

    assert profile_map['revenue']['business_name'] == 'Net Revenue'
    assert profile_map['revenue']['default_aggregation'] == 'average'
    assert profile_map['revenue']['override_source'] == 'manual'
    assert model['certified_metrics'] == ['revenue']
    assert model['manual_override_count'] == 1
