import pandas as pd

from insight_engine import (
    anomaly_insights,
    decomposition_insights,
    forecast_insights,
    funnel_insights,
    model_driver_insights,
    retention_cohort_insights,
    segment_driver_insights,
    variance_explanation_insights,
)


def test_funnel_insights_detect_weak_stage_conversion():
    df = pd.DataFrame({
        'visits': [100, 100],
        'signups': [50, 40],
        'purchase': [10, 5],
    })

    insights = funnel_insights(df, ['visits', 'signups', 'purchase'])

    assert insights
    assert insights[0]['kind'] == 'funnel'
    assert 'signups' in insights[0]['title']


def test_variance_explanation_identifies_segment_delta():
    df = pd.DataFrame({
        'date': pd.to_datetime(['2026-01-01', '2026-01-02', '2026-02-01', '2026-02-02'] * 4),
        'segment': ['A', 'B', 'A', 'B'] * 4,
        'revenue': [100, 100, 200, 110] * 4,
    })

    insights = variance_explanation_insights(df, ['date'], ['segment'], ['revenue'])

    assert insights
    assert insights[0]['kind'] == 'variance_explanation'
    assert 'A' in insights[0]['detail']
    assert insights[0]['confidence_score'] >= 0


def test_retention_cohort_insights_find_repeat_activity():
    df = pd.DataFrame({
        'date': pd.to_datetime([
            '2026-01-01', '2026-01-02', '2026-01-03', '2026-01-04', '2026-01-05',
            '2026-02-01', '2026-02-02', '2026-02-03', '2026-02-04', '2026-02-05',
            '2026-03-01', '2026-03-02', '2026-03-03', '2026-03-04', '2026-03-05',
            '2026-04-01', '2026-04-02', '2026-04-03', '2026-04-04', '2026-04-05',
        ]),
        'customer_id': [
            'c1', 'c2', 'c3', 'c4', 'c5',
            'c1', 'c2', 'c6', 'c7', 'c8',
            'c1', 'c3', 'c6', 'c9', 'c10',
            'c2', 'c4', 'c7', 'c9', 'c10',
        ],
    })

    insights = retention_cohort_insights(df, ['date'], ['customer_id'])

    assert insights
    assert insights[0]['kind'] == 'retention_cohort'
    assert insights[0]['confidence_label'] in {'low', 'medium', 'high'}


def test_segment_driver_insights_include_statistical_context():
    df = pd.DataFrame({
        'segment': ['A'] * 12 + ['B'] * 12 + ['C'] * 12,
        'revenue': [220, 210, 215, 225, 218, 222, 216, 230, 228, 221, 219, 224] + [140, 135, 145, 138, 142, 136, 144, 141, 139, 137, 143, 140] + [90, 88, 92, 95, 91, 89, 93, 94, 90, 87, 96, 92],
    })

    insights = segment_driver_insights(df, ['segment'], ['revenue'])

    assert insights
    assert insights[0]['kind'] == 'segment_driver'
    assert insights[0]['p_value'] is not None
    assert insights[0]['confidence_score'] >= 60


def test_anomaly_insights_use_robust_outlier_detection():
    df = pd.DataFrame({
        'revenue': [100, 101, 99, 103, 102, 98, 97, 101, 100, 99, 102, 450],
    })

    insights = anomaly_insights(df, ['revenue'])

    assert insights
    assert insights[0]['kind'] == 'anomaly'
    assert insights[0]['stat'].startswith('1 outliers')


def test_model_driver_insights_surface_top_predictor():
    rows = []
    for index in range(60):
        ad_spend = 50 + index * 2
        region = 'North' if index % 3 == 0 else 'South'
        revenue = ad_spend * 4 + (40 if region == 'North' else 5)
        rows.append({'region': region, 'ad_spend': ad_spend, 'revenue': revenue})
    df = pd.DataFrame(rows)

    insights = model_driver_insights(df, ['region'], ['revenue', 'ad_spend'])

    assert insights
    assert insights[0]['kind'] == 'model_driver'
    assert 'R' in insights[0]['stat']
    assert insights[0]['confidence_score'] >= 60


def test_forecast_insights_project_directional_next_period():
    df = pd.DataFrame({
        'date': pd.date_range('2025-01-01', periods=12, freq='MS'),
        'revenue': [100, 108, 115, 123, 131, 140, 149, 157, 166, 176, 187, 199],
    })

    insights = forecast_insights(df, ['date'], ['revenue'])

    assert insights
    assert insights[0]['kind'] == 'forecast'
    assert 'projected' in insights[0]['title']


def test_decomposition_insights_surface_trend_and_recurring_pattern():
    df = pd.DataFrame({
        'date': pd.date_range('2024-01-01', periods=18, freq='MS'),
        'revenue': [100, 112, 125, 120, 132, 145, 140, 152, 166, 160, 173, 188, 182, 196, 211, 206, 220, 236],
    })

    insights = decomposition_insights(df, ['date'], ['revenue'])

    assert insights
    assert insights[0]['kind'] == 'decomposition'
    assert 'seasonal range' in insights[0]['stat']
