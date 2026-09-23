"""Read Claude Code usage from its statusLine payload and the bridge cache."""

from common import (
    LEGACY_CACHE_NAME,
    MINUTES_PER_DAY,
    MINUTES_PER_HOUR,
    cache_dir,
    claude_cache_path,
    non_negative_number,
    read_json,
)

CLAUDE_CONTEXT_FIELDS = (
    'input_tokens',
    'cache_creation_input_tokens',
    'cache_read_input_tokens',
)
CLAUDE_RATE_LIMITS = (
    ('five_hour', 'primary', 5 * MINUTES_PER_HOUR),
    ('seven_day', 'secondary', 7 * MINUTES_PER_DAY),
    ('spend_limit', 'spend', None),
)


def claude_context_tokens(context):
    usage = context.get('current_usage')
    if isinstance(usage, dict):
        counts = [
            non_negative_number(usage.get(field))
            for field in CLAUDE_CONTEXT_FIELDS
        ]
        if any(count is not None for count in counts):
            return sum(count or 0 for count in counts)
    # An explicit null means startup or compaction, so show no stale usage.
    if 'current_usage' in context:
        return None
    percent = non_negative_number(context.get('used_percentage'))
    window = non_negative_number(context.get('context_window_size'))
    if percent is not None and window:
        return window * percent / 100
    return None


def claude_rate_limits(limits):
    result = {}
    for source, destination, minutes in CLAUDE_RATE_LIMITS:
        limit = limits.get(source) or {}
        percent = non_negative_number(limit.get('used_percentage'))
        if percent is not None:
            result[destination] = {
                'used_percent': percent,
                'window_minutes': minutes,
            }
    return result


def claude_state(data):
    """Normalize Claude's statusLine payload, dropping transcript text."""
    context = data.get('context_window') or {}
    model = data.get('model') or {}
    workspace = data.get('workspace') or {}
    cost = data.get('cost') or {}
    return {
        'app': 'claude',
        'session_id': data.get('session_id'),
        'cwd': workspace.get('current_dir') or data.get('cwd'),
        'model': model.get('display_name') or model.get('id'),
        'model_id': model.get('id'),
        'context': claude_context_tokens(context),
        'window': non_negative_number(context.get('context_window_size')),
        'rate_limits': claude_rate_limits(data.get('rate_limits') or {}),
        'estimated_cost_usd': non_negative_number(cost.get('total_cost_usd')),
    }


def cached_claude_state(token):
    legacy_cache = cache_dir().parent / LEGACY_CACHE_NAME
    legacy_path = legacy_cache / f'claude-{token}.json'
    return (
        read_json(claude_cache_path(token))
        or read_json(legacy_path)
        or {'app': 'claude'}
    )
