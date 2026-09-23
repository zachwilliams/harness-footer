#!/usr/bin/env python3
"""Render the shared Codex/Claude tmux status line from local usage data."""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import deque
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    'warn_tokens': 150_000,
    'target_tokens': 200_000,
    'head_width': 10,
    'tail_width': 5,
    'claude_show_builtin_status': True,
}
INTEGER_SETTINGS = ('warn_tokens', 'target_tokens', 'head_width', 'tail_width')
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
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR

PANE_APP_OPTION = '@harness-footer-app'
PANE_TOKEN_OPTION = '@harness-footer-token'
# Panes and caches created before the project was renamed.
LEGACY_PANE_APP_OPTION = '@agent-footer-app'
LEGACY_PANE_TOKEN_OPTION = '@agent-footer-token'
LEGACY_CACHE_NAME = 'agent-tmux'

CLAUDE_CONTEXT_FIELDS = (
    'input_tokens',
    'cache_creation_input_tokens',
    'cache_read_input_tokens',
)
CLAUDE_MODEL_ID = re.compile(
    r'claude-(opus|sonnet|haiku|fable)-(\d+)(?:[-.](\d{1,2}))?(?:[-@]|$)'
)
CLAUDE_RATE_LIMITS = (
    ('five_hour', 'primary', 5 * MINUTES_PER_HOUR),
    ('seven_day', 'secondary', 7 * MINUTES_PER_DAY),
    ('spend_limit', 'spend', None),
)


def command_output(args, timeout=2):
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=timeout, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        return ''
    return result.stdout.strip()


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def save_json(path, value):
    """Write atomically, readable only by the current user."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temp_name = tempfile.mkstemp(dir=path.parent, prefix='.footer-')
    try:
        with os.fdopen(fd, 'w') as file:
            json.dump(value, file)
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def load_settings():
    result = DEFAULTS | read_json(ROOT / 'settings.json')
    for key in INTEGER_SETTINGS:
        result[key] = int(result[key])
    if not 0 < result['warn_tokens'] < result['target_tokens']:
        raise ValueError('Require 0 < warn_tokens < target_tokens')
    if not 1 <= result['head_width'] <= 30:
        raise ValueError('Require 1 <= head_width <= 30')
    if not 0 <= result['tail_width'] <= 15:
        raise ValueError('Require 0 <= tail_width <= 15')
    return result


def cache_dir():
    base = os.environ.get('XDG_CACHE_HOME') or Path.home() / '.cache'
    return Path(base) / 'harness-footer'


def is_valid_token(token):
    return re.fullmatch(r'[a-f0-9]{32}', token or '') is not None


def claude_cache_path(token):
    return cache_dir() / f'claude-{token}.json'


def non_negative_number(value):
    """Return a finite number clamped to zero, or None for anything else."""
    is_number = isinstance(value, (int, float)) and not isinstance(value, bool)
    if is_number and math.isfinite(value):
        return max(0, value)
    return None


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


def context_color(used, window, config):
    percent = used / window * 100 if window else 0
    small_window = bool(window) and window < config['target_tokens']
    if used >= config['target_tokens'] or (
        small_window and percent >= ALERT_PERCENT
    ):
        return 'red'
    if used >= config['warn_tokens'] or (
        small_window and percent >= WARN_PERCENT
    ):
        return 'yellow'
    return 'green'


def context_segment(state, config, compact=False):
    used = non_negative_number(state.get('context'))
    window = non_negative_number(state.get('window'))
    if used is None:
        return style('ctx: awaiting usage', 'dim')
    color = context_color(used, window, config)
    label = format_tokens(used)
    if window:
        label += f' {used / window * 100:.0f}%'
    target = config['target_tokens']
    if compact:
        return style(f'ctx {label} / {format_tokens(target)}', color)

    head_span = min(target, window) if window else target
    gauge = style(bar(used, head_span, config['head_width']), color)
    gauge += style('│')
    if window and window > head_span:
        tail_width = config['tail_width']
        overflow = max(0, used - head_span)
        filled = filled_cells(overflow, window - head_span, tail_width)
        gauge += style('█' * filled, 'red')
        gauge += style('░' * (tail_width - filled), 'dim')
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


def git_output(cwd, *args):
    return command_output(['git', '--no-optional-locks', '-C', cwd, *args])


def git_segment(cwd):
    branch = git_output(
        cwd, 'symbolic-ref', '--quiet', '--short', 'HEAD'
    ) or git_output(cwd, 'rev-parse', '--short', 'HEAD')
    if not branch:
        return ''
    changes = git_output(
        cwd, 'status', '--porcelain', '--untracked-files=normal'
    )
    return f'[{branch}]*' if changes else f'[{branch}]'


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


def apply_rollout_event(state, event):
    event_type = event.get('type')
    payload = event.get('payload') or {}
    if not isinstance(payload, dict):
        return
    payload_type = payload.get('type')

    if event_type == 'session_meta':
        state.update(session_id=payload.get('id'), cwd=payload.get('cwd'))
    elif event_type == 'turn_context':
        if payload.get('model'):
            state['model'] = payload['model']
        if payload.get('cwd'):
            state['cwd'] = payload['cwd']
    elif event_type == 'compacted' or (
        event_type == 'event_msg' and payload_type == 'context_compacted'
    ):
        state.pop('context', None)
    elif event_type == 'token_usage_record':
        # Newer Codex versions write this before the matching token_count.
        last = payload.get('usage') or {}
        thread = payload.get('thread_token_usage') or {}
        if non_negative_number(last.get('total_tokens')) is not None:
            state['context'] = last['total_tokens']
        if non_negative_number(thread.get('total_tokens')) is not None:
            state['total'] = thread['total_tokens']
    elif event_type == 'event_msg' and payload_type == 'token_count':
        info = payload.get('info') or {}
        last = info.get('last_token_usage') or {}
        thread = info.get('total_token_usage') or {}
        updates = {
            'context': last.get('total_tokens'),
            'total': thread.get('total_tokens'),
            'window': info.get('model_context_window'),
        }
        for key, value in updates.items():
            if non_negative_number(value) is not None:
                state[key] = value
        if payload.get('rate_limits') is not None:
            state['rate_limits'] = payload['rate_limits']
        state['usage_at'] = event.get('timestamp')


def read_rollout(path, cache):
    """Apply rollout lines added since the last call to the cached state."""
    path = Path(path)
    key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
    cache_path = cache / f'rollout-{key}.json'
    cached = read_json(cache_path)
    stat = path.stat()
    identity = [stat.st_dev, stat.st_ino]
    is_stale = (
        cached.get('identity') != identity
        or cached.get('offset', 0) > stat.st_size
    )
    if is_stale:
        cached = {'identity': identity, 'offset': 0, 'state': {}}
    start = cached['offset']

    with path.open('rb') as file:
        file.seek(start)
        while line := file.readline():
            # The writer has not finished this line; retry on the next refresh.
            if not line.endswith(b'\n'):
                break
            cached['offset'] = file.tell()
            try:
                apply_rollout_event(cached['state'], json.loads(line))
            except (ValueError, TypeError, AttributeError):
                continue

    if cached['offset'] != start or not cache_path.exists():
        save_json(cache_path, cached)
    return cached['state']


def find_codex_pid(root_pid):
    """Breadth-first search of root_pid's process tree for a codex process."""
    names, children = {}, {}
    ps = command_output(['ps', '-axo', 'pid=,ppid=,comm='])
    for line in ps.splitlines():
        fields = line.split(None, 2)
        if len(fields) == 3:
            pid, parent = int(fields[0]), int(fields[1])
            names[pid] = Path(fields[2]).name
            children.setdefault(parent, []).append(pid)

    pending = deque([root_pid])
    while pending:
        pid = pending.popleft()
        if names.get(pid) == 'codex':
            return pid
        pending.extend(children.get(pid, []))
    return None


def is_cli_rollout(path):
    try:
        with path.open() as file:
            metadata = json.loads(file.readline()).get('payload', {})
    except (OSError, ValueError, AttributeError):
        return False
    return metadata.get('source') == 'cli'


def rollout_for_pid(pid):
    lsof = shutil.which('lsof')
    if pid is None or not lsof:
        return None
    open_files = command_output(
        [lsof, '-w', '-a', '-p', str(pid), '-Fn'], timeout=3
    )
    rollouts = [
        Path(line[1:])
        for line in open_files.splitlines()
        if line.startswith('n/')
        and '/rollout-' in line
        and line.endswith('.jsonl')
    ]
    rollouts = [path for path in rollouts if is_cli_rollout(path)]
    if not rollouts:
        return None
    # After /resume or /new, the old file can briefly remain open.
    return max(rollouts, key=lambda path: path.stat().st_mtime_ns)


def cached_claude_state(token):
    legacy_cache = cache_dir().parent / LEGACY_CACHE_NAME
    legacy_path = legacy_cache / f'claude-{token}.json'
    return (
        read_json(claude_cache_path(token))
        or read_json(legacy_path)
        or {'app': 'claude'}
    )


def tmux_pane_option(name, legacy_name):
    return f'#{{?{name},#{{{name}}},#{{{legacy_name}}}}}'


def pane_state(socket, pane):
    """Return the usage state and working directory for a tmux pane."""
    if not socket or not re.fullmatch(r'%\d+', pane or ''):
        raise ValueError('Pass --socket and --pane, or --rollout')
    pane_format = '\t'.join(
        [
            '#{pane_pid}',
            '#{pane_current_path}',
            tmux_pane_option(PANE_APP_OPTION, LEGACY_PANE_APP_OPTION),
            tmux_pane_option(PANE_TOKEN_OPTION, LEGACY_PANE_TOKEN_OPTION),
        ]
    )
    query = ['display-message', '-p', '-t', pane, pane_format]
    fields = command_output(['tmux', '-S', socket, *query])
    pid, cwd, app, token = fields.split('\t')
    if app == 'claude' and is_valid_token(token):
        return cached_claude_state(token), cwd
    rollout = rollout_for_pid(find_codex_pid(int(pid)))
    state = read_rollout(rollout, cache_dir()) if rollout else {}
    return state, cwd


def demo_state(app):
    state = {
        'app': app,
        'model': 'GPT-6-Astra' if app == 'codex' else 'Opus',
        'window': 1_000_000,
        'rate_limits': {
            'primary': {
                'used_percent': 57,
                'window_minutes': 7 * MINUTES_PER_DAY,
            }
        },
    }
    if app == 'codex':
        state['total'] = 42_000
    return state


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', help='tmux server socket path')
    parser.add_argument('--pane', help='tmux pane ID, such as %%1')
    parser.add_argument('--width', type=int, default=200)
    parser.add_argument(
        '--rollout', help='Explicit local transcript for preview/debugging'
    )
    parser.add_argument(
        '--plain', action='store_true', help='Omit tmux styles'
    )
    parser.add_argument(
        '--demo', type=int, help='Preview a context count in a 1M window'
    )
    parser.add_argument('--app', choices=['codex', 'claude'], default='codex')
    parser.add_argument(
        '--claude-json',
        help='Explicit statusLine payload for preview/debugging',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_settings()
    if args.demo is not None:
        state = demo_state(args.app) | {'context': args.demo}
        cwd, git = '/example/my-project', '[main]*'
    else:
        if args.claude_json:
            state, cwd = claude_state(read_json(args.claude_json)), os.getcwd()
        elif args.rollout:
            state, cwd = read_rollout(args.rollout, cache_dir()), os.getcwd()
        else:
            state, cwd = pane_state(args.socket, args.pane)
        cwd = state.get('cwd') or cwd
        git = git_segment(cwd)
    line = render(state, cwd, git, config, args.width)
    print(strip_styles(line) if args.plain else line)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # A missing or incompatible rollout must never break the terminal.
        print(style(' harness-footer: usage unavailable', 'dim'))
