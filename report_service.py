from html import escape
from typing import Any, Dict, List


def build_report_payload(summary: Dict[str, Any], dataset: Dict[str, Any] | None) -> Dict[str, Any]:
    dataset_name = 'Current dataset'
    if dataset:
        dataset_name = dataset.get('metadata', {}).get('display_name') or dataset.get('source_name', dataset_name)

    overview = summary.get('dataset_overview', {})
    takeaways = summary.get('executive_takeaways', [])
    insights = summary.get('key_insights', [])
    quality_alerts = summary.get('quality_alerts', [])

    sections = [
        {
            'title': 'Dataset overview',
            'bullets': [
                f"Dataset: {dataset_name}",
                f"Rows: {overview.get('rows', 0)}",
                f"Columns: {overview.get('columns', 0)}",
                f"Completeness: {overview.get('completeness_pct', 0)}%",
                f"Duplicate rows: {overview.get('duplicate_rows', 0)} ({overview.get('duplicate_pct', 0)}%)",
            ],
        },
        {
            'title': 'Executive takeaways',
            'bullets': takeaways or ['No executive takeaways were generated for this dataset.'],
        },
        {
            'title': 'Key analytical findings',
            'bullets': [
                f"{item['title']}: {item['detail']}"
                for item in insights[:6]
            ] or ['No key findings were generated.'],
        },
        {
            'title': 'Data quality notes',
            'bullets': [
                f"{item['title']}: {item['detail']}"
                for item in quality_alerts[:5]
            ] or ['No material data quality notes were detected.'],
        },
    ]

    markdown_lines: List[str] = [f"# Executive Summary: {dataset_name}", ""]
    for section in sections:
        markdown_lines.append(f"## {section['title']}")
        markdown_lines.extend([f"- {bullet}" for bullet in section['bullets']])
        markdown_lines.append("")

    safe_dataset_name = escape(dataset_name)
    html_sections = ''.join(
        (
            f"<div class='section'><h2>{escape(section['title'])}</h2><ul>"
            + ''.join(f"<li>{escape(str(bullet))}</li>" for bullet in section['bullets'])
            + "</ul></div>"
        )
        for section in sections
    )

    html = f"""
    <html>
    <head>
        <meta charset="utf-8">
        <title>Executive Summary - {safe_dataset_name}</title>
        <style>
            body {{ font-family: Arial, sans-serif; margin: 40px; color: #1f2937; }}
            h1, h2 {{ color: #111827; }}
            .section {{ margin-bottom: 28px; }}
            li {{ margin-bottom: 8px; }}
        </style>
    </head>
    <body>
        <h1>Executive Summary</h1>
        <p><strong>Dataset:</strong> {safe_dataset_name}</p>
        {html_sections}
    </body>
    </html>
    """

    return {
        'dataset_name': dataset_name,
        'sections': sections,
        'markdown': '\n'.join(markdown_lines).strip(),
        'html': html.strip(),
    }
