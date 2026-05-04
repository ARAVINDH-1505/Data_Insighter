from report_service import build_report_payload


def test_report_html_escapes_user_controlled_content():
    payload = build_report_payload(
        {
            'dataset_overview': {'rows': 1, 'columns': 1, 'completeness_pct': 100, 'duplicate_rows': 0, 'duplicate_pct': 0},
            'executive_takeaways': ['<script>alert(1)</script>'],
            'key_insights': [{'title': '<b>Unsafe</b>', 'detail': '<img src=x onerror=alert(1)>'}],
            'quality_alerts': [{'title': '<tag>', 'detail': '<svg/onload=alert(1)>'}],
        },
        {'source_name': '../../evil.csv', 'metadata': {'display_name': '<dataset>'}},
    )

    assert '<script>alert(1)</script>' not in payload['html']
    assert '&lt;script&gt;alert(1)&lt;/script&gt;' in payload['html']
    assert '&lt;dataset&gt;' in payload['html']
