"""Format usage state as a tmux status line that fits the terminal width."""

import math
import re
import unicodedata
from pathlib import Path

from common import MINUTES_PER_DAY, MINUTES_PER_HOUR, non_negative_number

COLORS = {
    'base': '#c0caf5',
    'dim': '#8b93b5',
    'green': '#9ece6a',
    'yellow': '#e0af68',
    'red': '#f7768e',
    'cyan': '#7dcfff',
}
BACKGROUND = '#1a1b26'
WARN_PERCENT = 50
ALERT_PERCENT = 80
CONTEXT_BAR_WIDTH = 20
CONTEXT_ALERT_FRACTION = 0.9
CLAUDE_MODEL_ID = re.compile(
    r'claude-(opus|sonnet|haiku|fable)-(\d+)(?:[-.](\d{1,2}))?(?:[-@]|$)'
)


def format_tokens(count):
    count = max(0, count)
    if count < 1000:
        return str(int(count))
    # Values that would round to "1000K" are shown as "1M" instead.
    if count >= 999_500:
        scale, suffix = 1_000_000, 'M'
    else:
        scale, suffix = 1000, 'K'
    value = count / scale
    if value < 9.95:
        return f'{value:.1f}'.rstrip('0').rstrip('.') + suffix
    return f'{value:.0f}{suffix}'


def sanitize(text):
    """Stop branch and directory names injecting terminal or tmux codes."""
    printable = (
        c for c in str(text) if not unicodedata.category(c).startswith('C')
    )
    return ''.join(printable).replace('#', '\N{FULLWIDTH NUMBER SIGN}')


def style(text, color='base', bold=False):
    if not text:
        return ''
    weight = ',bold' if bold else ''
    reset = ',nobold' if bold else ''
    return (
        f'#[fg={COLORS[color]}{weight}]{sanitize(text)}'
        f'#[fg={COLORS["base"]}{reset}]'
    )


def strip_styles(text):
    return re.sub(r'#\[[^\]]*\]', '', text)


def display_width(text):
    def char_width(char):
        if unicodedata.combining(char):
            return 0
        return 2 if unicodedata.east_asian_width(char) in 'WF' else 1

    return sum(char_width(char) for char in strip_styles(text))


def truncate(text, width):
    result = ''
    for char in text:
        if display_width(result + char) > width - 1:
            return result + '…'
        result += char
    return result


def filled_cells(used, span, width):
    return min(width, max(0, math.floor(used / max(1, span) * width + 0.5)))


def bar(used, span, width):
    filled = filled_cells(used, span, width)
    return '█' * filled + '░' * (width - filled)


def percent_color(percent):
    if percent >= ALERT_PERCENT:
        return 'red'
    if percent >= WARN_PERCENT:
        return 'yellow'
    return 'green'


def context_color(used, window, threshold):
    if window and used >= window * CONTEXT_ALERT_FRACTION:
        return 'red'
    if used >= threshold:
        return 'yellow'
    return 'green'


def context_gauge(used, window, threshold, color):
    """Draw usage across the whole window, with a marker at the threshold."""
    scale = window or threshold
    cells = bar(used, scale, CONTEXT_BAR_WIDTH)
    if threshold >= scale:
        return style(cells, color)
    marker = filled_cells(threshold, scale, CONTEXT_BAR_WIDTH)
    return (
        style(cells[:marker], color)
        + style('│')
        + style(cells[marker:], color)
    )


def context_segment(state, config, compact=False):
    used = non_negative_number(state.get('context'))
    window = non_negative_number(state.get('window'))
    if used is None:
        return style('ctx: awaiting usage', 'dim')
    threshold = config['context_threshold']
    color = context_color(used, window, threshold)
    label = format_tokens(used)
    if window:
        label += f' {used / window * 100:.0f}%'
    if compact:
        return style(f'ctx {label} / {format_tokens(threshold)}', color)
    gauge = context_gauge(used, window, threshold, color)
    return style('ctx[', color) + gauge + style(f'] {label}', color)


def window_label(minutes, fallback):
    if not minutes:
        return fallback
    for unit, suffix in ((MINUTES_PER_DAY, 'd'), (MINUTES_PER_HOUR, 'h')):
        if minutes % unit == 0:
            return f'{minutes / unit:g}{suffix}'
    return f'{minutes:g}m'


def quota_segment(state, compact=False):
    limits = state.get('rate_limits') or {}
    windows = []
    for key in ('primary', 'secondary', 'spend'):
        limit = limits.get(key) or {}
        percent = non_negative_number(limit.get('used_percent'))
        if percent is not None:
            minutes = non_negative_number(limit.get('window_minutes'))
            windows.append((window_label(minutes, key), percent))
    if not windows:
        return ''
    worst = max(percent for _, percent in windows)
    labels = ' '.join(f'{name} {percent:.0f}%' for name, percent in windows)
    gauge = '' if compact else f'[{bar(worst, 100, 10)}]'
    return style(f'quota{gauge} {labels}', percent_color(worst))


def cost_segment(state):
    cost = non_negative_number(state.get('estimated_cost_usd'))
    if cost is None:
        return ''
    # Claude reports a session estimate, not an invoiced or overage amount.
    return style(f'API est ${cost:.2f}', 'dim')


def model_name(state):
    name = state.get('model') or state.get('app') or 'codex'
    # Claude display names can already include a context-window suffix.
    name = re.sub(
        r'\s*\([\d.]+[km]\s*(?:context)?\)\s*$', '', name, flags=re.IGNORECASE
    )
    if state.get('app') == 'claude' and not re.search(r'\d', name):
        match = CLAUDE_MODEL_ID.match(state.get('model_id') or '')
        if match and match[1].lower() in name.lower():
            major, minor = match[2], match[3]
            name += f' {major}.{minor}' if minor else f' {major}'
    return name


def render(state, cwd, git, config, width=200):
    app = state.get('app') or 'codex'
    model = model_name(state)
    window = non_negative_number(state.get('window'))
    if window:
        model_with_window = f'{model} ({format_tokens(window).lower()})'
    else:
        model_with_window = model
    project = Path(cwd).name or cwd
    context = context_segment(state, config)
    compact_context = context_segment(state, config, compact=True)
    quota = quota_segment(state)
    compact_quota = quota_segment(state, compact=True)
    cost = cost_segment(state)

    layouts_widest_first = [
        [
            style(model_with_window, 'dim'),
            style(project, 'cyan'),
            style(git, 'dim'),
            context,
            quota,
            cost,
        ],
        [
            style(model, 'dim'),
            style(project, 'cyan'),
            style(git, 'dim'),
            context,
            quota,
            cost,
        ],
        [
            style(truncate(project, 16), 'cyan'),
            style(truncate(git, 25), 'dim'),
            context,
            compact_quota,
            cost,
        ],
        [style(truncate(git, 18), 'dim'), context, compact_quota, cost],
        [
            style(truncate(git, 12), 'dim'),
            compact_context,
            compact_quota,
            cost,
        ],
        [compact_context, compact_quota, cost],
        [compact_context, cost],
        [compact_context],
    ]
    prefix = style(app, 'cyan', bold=True)
    separator = style(' | ', 'dim')
    available = max(1, width - 2)
    for segments in layouts_widest_first:
        line = separator.join(s for s in [prefix, *segments] if s)
        if display_width(line) <= available:
            return ' ' + line
    clipped = truncate(strip_styles(line), available)
    app_length = len(app)
    app_part, rest = clipped[:app_length], clipped[app_length:]
    return ' ' + style(app_part, 'cyan', bold=True) + style(rest)
