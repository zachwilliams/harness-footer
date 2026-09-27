"""Read usage from the bridge directory omnigent writes for a harness.

Omnigent launches a real harness (`omnigent claude`, `omnigent cursor`) and
installs its own statusLine wrapper, which records the context window, cost
and model under `<tmp>/omnigent-<uid>/<harness>-native/<id>/`. Reading that
directory keeps the footer out of omnigent's way: it never has to win the
fight over Claude's single `--settings` value.
"""

import os
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

from harness_footer.claude_usage import claude_context_tokens, claude_state
from harness_footer.common import (
    cache_dir,
    command_output,
    non_negative_number,
    read_json,
    save_json,
)

LAUNCHER = 'omnigent'
# The raw statusLine capture comes first: it is the whole payload, so it
# still carries the rate limits that omnigent's normalized record drops.
CONTEXT_FILES = ('context_raw.json', 'context.json')
# Omnigent records the tmux server it runs a harness on beside the context
# file, which is how the footer finds the one bar it should silence.
TMUX_FILE = 'tmux.json'
SILENCED_CACHE = 'omnigent-silenced.json'


def omnigent_home():
    return Path.home() / f'.{LAUNCHER}'


def bridge_roots():
    """Return the omnigent runtime roots belonging to the current user."""
    # Omnigent namespaces its scratch directory by numeric uid on POSIX, and
    # names other temp directories `omnigent-terminal-*`, so match exactly.
    name = f'{LAUNCHER}-{os.getuid()}'
    # Harnesses do not agree on where their bridge lives: claude-native uses
    # the temp root, antigravity-native uses the home directory.
    candidates = [Path(tempfile.gettempdir()) / name, Path('/tmp') / name]
    candidates.append(omnigent_home())
    roots = []
    for root in candidates:
        try:
            if root.is_dir() and root.stat().st_uid == os.getuid():
                roots.append(root)
        except OSError:
            continue
    return sorted(set(roots))


def context_files():
    """Return (harness, path) for every bridge context file on disk."""
    found = []
    for root in bridge_roots():
        for harness_dir in sorted(root.glob('*-native')):
            harness = harness_dir.name.removesuffix('-native')
            try:
                bridges = sorted(harness_dir.iterdir())
            except OSError:
                continue
            for bridge in bridges:
                for name in CONTEXT_FILES:
                    path = bridge / name
                    if path.is_file():
                        found.append((harness, path))
                        break  # One record per bridge, raw preferred.
    return found


def remote_server():
    """Return the server host when omnigent's host daemon is bound to one."""
    daemons = omnigent_home() / 'daemons'
    try:
        records = sorted(daemons.glob('*.json'))
    except OSError:
        return None
    for path in records:
        record = read_json(path)
        if record.get('mode') != 'server':
            continue
        host = urlsplit(record.get('server_url') or '').hostname
        pid = record.get('pid')
        if not host or not isinstance(pid, int):
            continue
        try:
            os.kill(pid, 0)  # Signal 0 only tests that the daemon is alive.
        except (OSError, ValueError):
            continue
        return host
    return None


def hide_inner_status_bar(state):
    """Turn off the status bar of the tmux server omnigent runs this on.

    In server mode omnigent gives each harness a private tmux server with a
    status bar of its own, which stacks a second bar inside the footer's
    session. Only the server backing this pane's session is silenced, so
    omnigent sessions running outside the footer keep their own bar.
    """
    bridge = state.get('bridge_dir')
    if not bridge:
        return
    socket = read_json(Path(bridge) / TMUX_FILE).get('socket_path')
    if not socket or not Path(socket).exists():
        return
    cache_path = cache_dir() / SILENCED_CACHE
    silenced = set(read_json(cache_path).get('sockets') or [])
    if socket in silenced:
        return
    command_output(['tmux', '-S', socket, 'set-option', '-g', 'status', 'off'])
    # Drop servers that have gone away so the list cannot grow forever.
    silenced = {path for path in silenced if Path(path).exists()}
    save_json(cache_path, {'sockets': sorted(silenced | {socket})})


def normalized_state(harness, record):
    """Read omnigent's own `context.json`, which has no rate limits."""
    model = record.get('model')
    return {
        'app': harness,
        'launcher': LAUNCHER,
        'model': model,
        'model_id': model,
        'context': claude_context_tokens(record),
        'window': non_negative_number(record.get('context_window_size')),
        'estimated_cost_usd': non_negative_number(
            record.get('total_cost_usd')
        ),
    }


def bridge_state(harness, record):
    # `context_raw.json` is the harness payload verbatim, so the Claude
    # reader already understands every field, rate limits included.
    if 'context_window' in record:
        return claude_state(record) | {'app': harness, 'launcher': LAUNCHER}
    return normalized_state(harness, record)


def omnigent_state(cwd=None):
    """Return usage from the bridge whose harness is working in cwd."""
    best, best_rank = {}, None
    for harness, path in context_files():
        record = read_json(path)
        if not isinstance(record, dict) or not record:
            continue
        try:
            modified = path.stat().st_mtime_ns
        except OSError:
            continue
        state = bridge_state(harness, record) | {
            'bridge_dir': str(path.parent)
        }
        # Several harnesses can be live at once, so prefer the one whose
        # own payload reports the directory this pane is sitting in.
        rank = (bool(cwd) and state.get('cwd') == cwd, modified)
        if best_rank is None or rank > best_rank:
            best, best_rank = state, rank
    if best:
        best['remote'] = remote_server()
    return best
