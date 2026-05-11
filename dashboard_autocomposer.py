from __future__ import annotations

from typing import Any, Dict, List


def _text_block(block_id: int, title: str, content: str, x: int, y: int, width: int, height: int, *, emphasis: bool = False) -> Dict[str, Any]:
    return {
        'id': block_id,
        'title': title,
        'type': 'text',
        'columns': [],
        'content': content,
        'position': {'x': x, 'y': y},
        'size': {'width': width, 'height': height},
        'style': {
            'fontFamily': "'Plus Jakarta Sans', 'Segoe UI', sans-serif",
            'fontSize': '15px',
            'fontWeight': '600' if emphasis else '500',
            'color': '',
            'backgroundColor': 'transparent',
        },
    }


def _chart_block(block_id: int, title: str, chart: Dict[str, Any], x: int, y: int, width: int, height: int) -> Dict[str, Any]:
    return {
        'id': block_id,
        'title': title,
        'type': chart.get('type', 'bar'),
        'columns': chart.get('columns', []),
        'samplePercentage': chart.get('sample_percentage', 100),
        'position': {'x': x, 'y': y},
        'size': {'width': width, 'height': height},
    }


def compose_starter_dashboard(summary: Dict[str, Any], dataset_name: str | None = None) -> Dict[str, Any]:
    semantic_model = summary.get('semantic_model', {}) or {}
    key_insights = summary.get('key_insights', []) or []
    takeaways = summary.get('executive_takeaways', []) or []

    recommended_measures = semantic_model.get('recommended_measures', []) or []
    recommended_dimensions = semantic_model.get('recommended_dimensions', []) or []
    primary_date = semantic_model.get('primary_date_column')

    insight_charts = []
    for insight in key_insights:
        chart = insight.get('recommended_chart')
        if chart and chart.get('columns'):
            insight_charts.append((insight, chart))

    def matches_chart(chart: Dict[str, Any], chart_type: str | None = None) -> bool:
        if chart_type and chart.get('type') != chart_type:
            return False
        return bool(chart.get('columns'))

    used_keys: set[tuple[Any, ...]] = set()

    def claim_chart(chart: Dict[str, Any]) -> Dict[str, Any] | None:
        key = (chart.get('type'), tuple(chart.get('columns', [])))
        if key in used_keys:
            return None
        used_keys.add(key)
        return chart

    def pick_chart(*, insight_kind: str | None = None, chart_type: str | None = None) -> Dict[str, Any] | None:
        if insight_kind:
            for insight, chart in insight_charts:
                if insight.get('kind') == insight_kind and matches_chart(chart, chart_type):
                    claimed = claim_chart(chart)
                    if claimed:
                        return claimed
        for chart in summary.get('recommended_visualizations', []) or []:
            if matches_chart(chart, chart_type):
                claimed = claim_chart(chart)
                if claimed:
                    return claimed
        for _, chart in insight_charts:
            if matches_chart(chart, chart_type):
                claimed = claim_chart(chart)
                if claimed:
                    return claimed
        return None

    cards: List[Dict[str, Any]] = []
    next_id = 1000

    dataset_label = dataset_name or 'Active dataset'
    focus_metrics = ', '.join(recommended_measures[:2]) if recommended_measures else 'the main business metrics'
    focus_dims = ', '.join(recommended_dimensions[:2]) if recommended_dimensions else 'the strongest segments'
    header_lines = [
        f'{dataset_label} / {semantic_model.get("dataset_role", "dataset").title()} model',
        f'Row grain: {semantic_model.get("row_grain", "unspecified")}',
        f'Primary focus: {focus_metrics}',
        f'Key slicing dimensions: {focus_dims}',
    ]
    if primary_date:
        header_lines.append(f'Primary time axis: {primary_date}')
    cards.append(_text_block(next_id, 'Dashboard brief', '\n'.join(header_lines), 24, 24, 1210, 120, emphasis=True))
    next_id += 1

    primary_measure = recommended_measures[0] if recommended_measures else None
    secondary_measure = recommended_measures[1] if len(recommended_measures) > 1 else None
    primary_dimension = recommended_dimensions[0] if recommended_dimensions else None

    if primary_measure:
        cards.append(_chart_block(next_id, f'{primary_measure} KPI', {
            'type': 'kpi',
            'columns': [primary_measure],
            'sample_percentage': 100,
        }, 24, 164, 250, 220))
        next_id += 1

    if secondary_measure:
        cards.append(_chart_block(next_id, f'{secondary_measure} KPI', {
            'type': 'kpi',
            'columns': [secondary_measure],
            'sample_percentage': 100,
        }, 294, 164, 250, 220))
        next_id += 1

    if primary_measure and primary_date:
        cards.append(_chart_block(next_id, f'{primary_measure} trend', {
            'type': 'line',
            'columns': [primary_date, primary_measure],
            'sample_percentage': 100,
        }, 564, 164, 670, 300))
        next_id += 1
    else:
        fallback_chart = pick_chart(chart_type='kpi') or pick_chart(chart_type='line') or pick_chart()
        if fallback_chart:
            cards.append(_chart_block(next_id, fallback_chart.get('title', 'Headline chart'), fallback_chart, 564, 164, 670, 300))
            next_id += 1

    section_note = 'Use this row to compare which segments outperform, concentrate value, or explain recent movement.'
    cards.append(_text_block(next_id, 'Driver row', section_note, 24, 404, 520, 78))
    next_id += 1

    comparison_chart = pick_chart(insight_kind='segment_driver', chart_type='bar')
    if not comparison_chart and primary_dimension and primary_measure:
        comparison_chart = {
            'type': 'bar',
            'columns': [primary_dimension, primary_measure],
            'sample_percentage': 100,
        }
    if comparison_chart:
        cards.append(_chart_block(next_id, comparison_chart.get('title', 'Segment comparison'), comparison_chart, 24, 496, 520, 300))
        next_id += 1

    contribution_chart = pick_chart(insight_kind='contribution', chart_type='bar') or pick_chart(insight_kind='variance_explanation', chart_type='bar')
    if contribution_chart:
        cards.append(_chart_block(next_id, contribution_chart.get('title', 'Contribution view'), contribution_chart, 564, 496, 670, 300))
        next_id += 1

    watchlist_chart = (
        pick_chart(insight_kind='anomaly', chart_type='box')
        or pick_chart(insight_kind='seasonality', chart_type='line')
        or pick_chart(insight_kind='correlation', chart_type='scatter')
        or pick_chart(chart_type='heatmap')
    )
    if watchlist_chart:
        cards.append(_chart_block(next_id, watchlist_chart.get('title', 'Watchlist'), watchlist_chart, 24, 824, 520, 300))
        next_id += 1

    takeaway_lines = takeaways[:4] or ['No narrative takeaways were generated yet.']
    cards.append(_text_block(next_id, 'Executive takeaways', '\n'.join(f'- {line}' for line in takeaway_lines), 564, 824, 670, 300))

    return {
        'layout_name': 'executive_storyboard',
        'dashboard_viz': cards,
    }
