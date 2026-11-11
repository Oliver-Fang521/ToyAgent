"""Allowlisted metadata for model-call diagnostics; never include prompts or bodies."""
from gaia_agent.interactive.model_registry import MODEL_ROWS

ALLOWED_MODELS = frozenset(row[0] for row in MODEL_ROWS) | {'deepseek-flash', 'deepseek-v4-pro', 'unknown'}
ALLOWED_EFFORTS = frozenset({'none','minimal','low','medium','high','xhigh','max','unspecified'})
METRIC_FIELDS = frozenset({'model','reasoning_effort','max_output_tokens','output_tokens','reasoning_tokens'})


def safe_model_metrics(payload):
    result = {}
    if isinstance(payload.get('model'), str) and payload['model'] in ALLOWED_MODELS:
        result['model'] = payload['model']
    if isinstance(payload.get('reasoning_effort'), str) and payload['reasoning_effort'] in ALLOWED_EFFORTS:
        result['reasoning_effort'] = payload['reasoning_effort']
    for key in ('max_output_tokens','output_tokens','reasoning_tokens'):
        value = payload.get(key)
        if type(value) is int and 0 <= value <= 10_000_000:
            result[key] = value
    return result
