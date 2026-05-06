from typing import Any, Dict, List

from dataset_pipeline_service import supports_pipeline_rebuild


RESTRICTED_HINTS = {
    'ssn', 'social_security', 'aadhaar', 'passport', 'credit_card', 'card_number',
    'cvv', 'iban', 'swift', 'routing', 'tax_id', 'pan_number',
}
CONFIDENTIAL_HINTS = {
    'email', 'phone', 'mobile', 'address', 'full_name', 'first_name', 'last_name',
    'customer_name', 'birth', 'dob', 'salary', 'payroll', 'compensation',
}


def _column_hints(summary: Dict[str, Any]) -> List[str]:
    profiles = summary.get('semantic_profiles', []) or []
    return [str(profile.get('name', '')).lower() for profile in profiles]


def infer_sensitivity_label(summary: Dict[str, Any]) -> Dict[str, Any]:
    names = _column_hints(summary)
    matched_restricted = sorted({hint for hint in RESTRICTED_HINTS if any(hint in name for name in names)})
    matched_confidential = sorted({hint for hint in CONFIDENTIAL_HINTS if any(hint in name for name in names)})

    if matched_restricted:
        return {
            'label': 'Restricted',
            'reasons': [
                f"Detected highly sensitive fields related to {', '.join(matched_restricted[:4])}."
            ],
        }
    if matched_confidential:
        return {
            'label': 'Confidential',
            'reasons': [
                f"Detected personal or business-sensitive fields related to {', '.join(matched_confidential[:4])}."
            ],
        }

    if summary.get('dataset_overview', {}).get('numeric_columns', 0) > 0:
        return {
            'label': 'General',
            'reasons': ['No strong sensitive-data signals were inferred from field names or semantic roles.'],
        }

    return {
        'label': 'Public',
        'reasons': ['The dataset looks broadly shareable based on the currently inferred schema signals.'],
    }


def build_governance_summary(
    dataset_record: Dict[str, Any],
    analysis_summary: Dict[str, Any],
    audit_events: List[Dict[str, Any]],
    dashboards: List[Dict[str, Any]],
    measures: List[Dict[str, Any]],
    reports: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    sensitivity = infer_sensitivity_label(analysis_summary)
    alerts = analysis_summary.get('quality_alerts', []) or []
    severe_warnings = [alert for alert in alerts if alert.get('severity') == 'warning']
    pipeline_steps = dataset_record.get('metadata', {}).get('pipeline_steps', []) or []
    lineage_steps = dataset_record.get('metadata', {}).get('lineage_steps', []) or []

    if severe_warnings:
        quality_risk = 'Medium'
    else:
        quality_risk = 'Low'
    if len(severe_warnings) >= 3:
        quality_risk = 'High'

    return {
        'sensitivity': sensitivity,
        'quality_risk': quality_risk,
        'lineage_depth': len(lineage_steps),
        'pipeline': {
            'step_count': len(pipeline_steps),
            'can_rebuild': supports_pipeline_rebuild(dataset_record),
            'last_recorded_step': pipeline_steps[-1] if pipeline_steps else None,
        },
        'downstream_assets': {
            'dashboards': len(dashboards),
            'measures': len(measures),
            'reports': len(reports or []),
        },
        'activity': audit_events[:8],
    }
