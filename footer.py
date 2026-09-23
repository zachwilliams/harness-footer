#!/usr/bin/env python3
"""Shared Codex/Claude tmux renderer; reads local usage only."""
import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unicodedata

ROOT = Path(__file__).resolve().parent
DEFAULTS = {'warn_tokens': 150000, 'target_tokens': 200000,
            'head_width': 10, 'tail_width': 5}
COLORS = {'base': '#c0caf5', 'dim': '#8b93b5', 'green': '#9ece6a',
          'yellow': '#e0af68', 'red': '#f7768e', 'cyan': '#7dcfff'}


def run(args, timeout=2):
    try:
        return subprocess.run(args, capture_output=True, text=True, timeout=timeout,
                              check=False).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ''


def read_json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.footer-')
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f)
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def settings():
    result = DEFAULTS | read_json(ROOT / 'settings.example.json') | read_json(ROOT / 'settings.json')
    for key in DEFAULTS:
        result[key] = int(result[key])
    if not 0 < result['warn_tokens'] < result['target_tokens']:
        raise ValueError('Require 0 < warn_tokens < target_tokens')
    if not 1 <= result['head_width'] <= 30 or not 0 <= result['tail_width'] <= 15:
        raise ValueError('Invalid gauge widths')
    return result


def number(value):
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return max(0, value)
    return None


def fmt(n):
    n = max(0, n)
    if n < 1000:
        return str(int(n))
    scale, suffix = (1000000, 'M') if n >= 999500 else (1000, 'K')
    v = n / scale
    return (f'{v:.1f}'.rstrip('0').rstrip('.') if v < 9.95 else f'{v:.0f}') + suffix


def safe(text):
    # Prevent branch/directory names from injecting terminal or tmux formatting.
    return ''.join(c for c in str(text) if not unicodedata.category(c).startswith('C')).replace('#', '＃')


def style(text, color='base', bold=False):
    weight = ',bold' if bold else ''
    reset = ',nobold' if bold else ''
    return f'#[fg={COLORS[color]}{weight}]{safe(text)}#[fg={COLORS["base"]}{reset}]'


def plain(text):
    return re.sub(r'#\[[^\]]*\]', '', text)


def cells(text):
    return sum(0 if unicodedata.combining(c) else 2 if unicodedata.east_asian_width(c) in 'WF' else 1
               for c in plain(text))


def shorten(text, width):
    out = ''
    for c in text:
        if cells(out + c) > width - 1:
            return out + '…'
        out += c
    return out


def bar(used, span, width):
    filled = min(width, max(0, math.floor(used / max(1, span) * width + 0.5)))
    return '█' * filled + '░' * (width - filled)


def context_segment(state, config, compact=False):
    used, window = number(state.get('context')), number(state.get('window'))
    if used is None:
        return style('ctx: awaiting usage', 'dim')
    pct = used / window * 100 if window else None
    small_window = window is not None and window < config['target_tokens']
    color = ('red' if used >= config['target_tokens'] or (small_window and pct is not None and pct >= 80)
             else 'yellow' if used >= config['warn_tokens'] or (small_window and pct is not None and pct >= 50)
             else 'green')
    label = fmt(used) + (f' {pct:.0f}%' if pct is not None else '')
    if compact:
        return style(f'ctx {label} / {fmt(config["target_tokens"])}', color)
    span = min(config['target_tokens'], window) if window else config['target_tokens']
    gauge = style(bar(used, span, config['head_width']), color) + style('│')
    if window and window > span:
        tail = max(0, used - span)
        filled = min(config['tail_width'], math.floor(tail / (window - span) * config['tail_width'] + 0.5))
        gauge += style('█' * filled, 'red') + style('░' * (config['tail_width'] - filled), 'dim')
    return style('ctx[', color) + gauge + style(f'] {label}', color)


def quota_segment(state, compact=False):
    limits = state.get('rate_limits') or {}
    windows = []
    for key in ['primary', 'secondary', 'spend']:
        w = limits.get(key) or {}
        pct = number(w.get('used_percent'))
        if pct is None:
            continue
        mins = number(w.get('window_minutes'))
        label = (f'{mins / 1440:g}d' if mins and mins % 1440 == 0 else
                 f'{mins / 60:g}h' if mins and mins % 60 == 0 else
                 f'{mins:g}m' if mins else key)
        windows.append((pct, label))
    if not windows:
        return ''
    worst = max(p for p, _ in windows)
    color = 'red' if worst >= 80 else 'yellow' if worst >= 50 else 'green'
    label = ' '.join(f'{name} {pct:.0f}%' for pct, name in windows)
    gauge = '' if compact else '[' + bar(worst, 100, 10) + ']'
    return style(f'quota{gauge} {label}', color)


def cost_segment(state):
    cost = number(state.get('estimated_cost_usd'))
    if cost is None:
        return ''
    # Claude reports a session estimate, not an overage-only or invoiced amount.
    return style(f'API est ${cost:.2f}', 'dim')


def model_name(state):
    name = state.get('model') or state.get('app') or 'codex'
    # Claude display names can already include a context-window suffix.
    name = re.sub(r'\s*\([\d.]+[km]\s*(?:context)?\)\s*$', '', name, flags=re.I)
    if state.get('app') == 'claude' and not re.search(r'\d', name):
        match = re.match(r'claude-(opus|sonnet|haiku|fable)-(\d+)(?:[-.](\d{1,2}))?(?:[-@]|$)',
                         state.get('model_id') or '')
        if match and match[1].lower() in name.lower():
            name += ' ' + match[2] + ('.' + match[3] if match[3] else '')
    return name


def git_segment(cwd):
    branch = run(['git', '--no-optional-locks', '-C', cwd, 'symbolic-ref', '--quiet', '--short', 'HEAD'])
    if not branch:
        branch = run(['git', '--no-optional-locks', '-C', cwd, 'rev-parse', '--short', 'HEAD'])
    if not branch:
        return ''
    # Includes staged, unstaged and untracked files; ignores ignored files.
    dirty = run(['git', '--no-optional-locks', '-C', cwd, 'status', '--porcelain', '--untracked-files=normal'])
    return f'[{branch}]' + ('*' if dirty else '')


def render(state, cwd, git, config, width=200):
    app = state.get('app') or 'codex'
    model = model_name(state)
    window = number(state.get('window'))
    model_full = model + (f' ({fmt(window).lower()})' if window else '')
    project = Path(cwd).name or cwd
    ctx = context_segment(state, config)
    quota = quota_segment(state)
    cost = cost_segment(state)
    sep = style(' | ', 'dim')
    variants = [
        [style(model_full, 'dim'), style(project, 'cyan'), style(git, 'dim') if git else '',
         ctx, quota, cost],
        [style(model, 'dim'), style(project, 'cyan'), style(git, 'dim') if git else '', ctx, quota, cost],
        [style(shorten(project, 16), 'cyan'), style(shorten(git, 25), 'dim') if git else '',
         ctx, quota_segment(state, compact=True), cost],
        [style(shorten(git, 18), 'dim') if git else '', ctx, quota_segment(state, compact=True), cost],
        [style(shorten(git, 12), 'dim') if git else '', context_segment(state, config, compact=True),
         quota_segment(state, compact=True), cost],
        [context_segment(state, config, compact=True), quota_segment(state, compact=True), cost],
        [context_segment(state, config, compact=True), cost],
        [context_segment(state, config, compact=True)],
    ]
    for parts in variants:
        result = sep.join(p for p in [style(app, 'cyan', bold=True), *parts] if p)
        if cells(result) <= max(1, width - 2):
            return ' ' + result
    clipped = shorten(plain(result), max(1, width - 2))
    return ' ' + style(clipped[:len(app)], 'cyan', bold=True) + style(clipped[len(app):])


def cache_dir():
    return Path(os.environ.get('XDG_CACHE_HOME', str(Path.home() / '.cache'))) / 'harness-footer'


def claude_state(data):
    """Normalize the documented statusLine payload, never retaining transcript text."""
    context = data.get('context_window') or {}
    window = number(context.get('context_window_size'))
    usage = context.get('current_usage')
    used = None
    if isinstance(usage, dict):
        values = [number(usage.get(k)) for k in
                  ('input_tokens', 'cache_creation_input_tokens', 'cache_read_input_tokens')]
        if any(v is not None for v in values):
            used = sum(v or 0 for v in values)
    # Explicit null means startup or compaction, so don't retain old usage.
    if used is None and 'current_usage' not in context:
        pct = number(context.get('used_percentage'))
        if pct is not None and window:
            used = window * pct / 100
    limits = data.get('rate_limits') or {}
    rate_limits = {}
    for source, dest, minutes in [('five_hour', 'primary', 300), ('seven_day', 'secondary', 10080),
                                  ('spend_limit', 'spend', None)]:
        pct = number((limits.get(source) or {}).get('used_percentage'))
        if pct is not None:
            rate_limits[dest] = {'used_percent': pct, 'window_minutes': minutes}
    model = data.get('model') or {}
    return {'app': 'claude', 'session_id': data.get('session_id'),
            'cwd': (data.get('workspace') or {}).get('current_dir') or data.get('cwd'),
            'model': model.get('display_name') or model.get('id'),
            'model_id': model.get('id'),
            'context': used, 'window': window, 'rate_limits': rate_limits,
            'estimated_cost_usd': number((data.get('cost') or {}).get('total_cost_usd'))}


def consume(state, event):
    typ = event.get('type')
    payload = event.get('payload') or {}
    if not isinstance(payload, dict):
        return
    if typ == 'session_meta':
        state.update(session_id=payload.get('id'), cwd=payload.get('cwd'))
    elif typ == 'turn_context':
        if payload.get('model'):
            state['model'] = payload['model']
        if payload.get('cwd'):
            state['cwd'] = payload['cwd']
    elif typ == 'compacted' or (typ == 'event_msg' and payload.get('type') == 'context_compacted'):
        state.pop('context', None)
    elif typ == 'token_usage_record':
        # Newer Codex records this before the corresponding token_count event.
        usage = payload.get('usage') or {}
        total = payload.get('thread_token_usage') or {}
        if number(usage.get('total_tokens')) is not None:
            state['context'] = usage['total_tokens']
        if number(total.get('total_tokens')) is not None:
            state['total'] = total['total_tokens']
    elif typ == 'event_msg' and payload.get('type') == 'token_count':
        info = payload.get('info') or {}
        for dest, value in [('context', (info.get('last_token_usage') or {}).get('total_tokens')),
                            ('total', (info.get('total_token_usage') or {}).get('total_tokens')),
                            ('window', info.get('model_context_window'))]:
            if number(value) is not None:
                state[dest] = value
        if payload.get('rate_limits') is not None:
            state['rate_limits'] = payload['rate_limits']
        state['usage_at'] = event.get('timestamp')


def read_rollout(path, cache_dir):
    path = Path(path)
    key = hashlib.sha256(str(path).encode()).hexdigest()[:24]
    cache_path = cache_dir / f'rollout-{key}.json'
    cached = read_json(cache_path)
    stat = path.stat()
    identity = [stat.st_dev, stat.st_ino]
    if cached.get('identity') != identity or cached.get('offset', 0) > stat.st_size:
        cached = {'identity': identity, 'offset': 0, 'state': {}}
    state = cached['state']
    start = cached['offset']
    with path.open('rb') as f:
        f.seek(start)
        while line := f.readline():
            if not line.endswith(b'\n'):
                break  # Writer has not finished this record; retry on next refresh.
            cached['offset'] = f.tell()
            try:
                event = json.loads(line)
                if isinstance(event, dict):
                    consume(state, event)
            except (ValueError, TypeError):
                continue
    if cached['offset'] != start or not cache_path.exists():
        save_json(cache_path, cached)
    return state


def find_codex(pane_pid):
    rows = []
    for line in run(['ps', '-axo', 'pid=,ppid=,comm=']).splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) == 3:
            rows.append((int(parts[0]), int(parts[1]), Path(parts[2]).name))
    frontier = [pane_pid]
    seen = set()
    while frontier:
        for pid in frontier:
            for row in rows:
                if row[0] == pid and row[2] == 'codex':
                    return pid
        seen.update(frontier)
        frontier = [pid for pid, parent, _ in rows if parent in frontier and pid not in seen]
    return None


def rollout_for_pid(pid):
    lsof = shutil.which('lsof')
    if pid is None or not lsof:
        return None
    paths = []
    for line in run([lsof, '-w', '-a', '-p', str(pid), '-Fn'], timeout=3).splitlines():
        if not line.startswith('n/') or '/rollout-' not in line or not line.endswith('.jsonl'):
            continue
        p = Path(line[1:])
        try:
            with p.open() as f:
                meta = json.loads(f.readline()).get('payload', {})
            if meta.get('source') == 'cli':
                paths.append(p)
        except (OSError, ValueError):
            pass
    # On /resume or /new, a process can briefly retain the old file handle.
    return max(paths, key=lambda p: p.stat().st_mtime_ns) if paths else None


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--socket')
    parser.add_argument('--pane')
    parser.add_argument('--width', type=int, default=200)
    parser.add_argument('--rollout', help='Explicit local transcript for preview/debugging')
    parser.add_argument('--plain', action='store_true')
    parser.add_argument('--demo', type=int, help='Preview a context count in a 1M window')
    parser.add_argument('--app', choices=['codex', 'claude'], default='codex')
    parser.add_argument('--claude-json', help='Explicit statusLine payload for preview/debugging')
    args = parser.parse_args()
    cache = cache_dir()
    config = settings()
    cwd = os.getcwd()
    if args.demo is not None:
        state = {'app': args.app, 'model': 'GPT-6-Astra' if args.app == 'codex' else 'Opus',
                 'context': args.demo, 'window': 1000000,
                 'rate_limits': {'primary': {'used_percent': 57, 'window_minutes': 10080}}}
        if args.app == 'codex':
            state['total'] = 42000
        cwd, git = '/example/my-project', '[main]*'
    elif args.claude_json:
        state = claude_state(read_json(args.claude_json))
        cwd = state.get('cwd') or cwd
        git = git_segment(cwd)
    else:
        path = args.rollout
        state = None
        if not path:
            if not args.socket or not re.fullmatch(r'%\d+', args.pane or ''):
                raise ValueError('Pass --socket and --pane, or --rollout')
            info = run(['tmux', '-S', args.socket, 'display-message', '-p', '-t', args.pane,
                        '#{pane_pid}\t#{pane_current_path}\t'
                        '#{?@harness-footer-app,#{@harness-footer-app},#{@agent-footer-app}}\t'
                        '#{?@harness-footer-token,#{@harness-footer-token},#{@agent-footer-token}}'])
            pid, cwd, app, token = info.split('\t')
            if app == 'claude' and re.fullmatch(r'[a-f0-9]{32}', token):
                state = (read_json(cache / f'claude-{token}.json') or
                         read_json(cache.parent / 'agent-tmux' / f'claude-{token}.json') or
                         {'app': 'claude'})
            else:
                path = rollout_for_pid(find_codex(int(pid)))
        if state is None:
            state = read_rollout(path, cache) if path else {}
        cwd = state.get('cwd') or cwd
        git = git_segment(cwd)
    result = render(state, cwd, git, config, args.width)
    print(plain(result) if args.plain else result)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # A missing/incompatible rollout must never break the terminal session.
        print(style(' Agent footer: usage unavailable', 'dim'))
