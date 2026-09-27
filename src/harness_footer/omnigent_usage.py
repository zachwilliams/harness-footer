"""Read usage from the bridge directory omnigent writes for a harness.

Omnigent launches a real harness (`omnigent claude`, `omnigent cursor`) and
installs its own statusLine wrapper, which records the context window, cost
and model under `<tmp>/omnigent-<uid>/<harness>-native/<id>/`. Reading that
directory keeps the footer out of omnigent's way: it never has to win the
fight over Claude's single `--settings` value.
"""

import os
import re
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
# claude-native records its tmux server here; the other harnesses record
# none, and are matched to one by the runner pid that owns both.
TMUX_FILE = 'tmux.json'
OWNER_FILE = 'owner.pid'
TERMINAL_GLOB = f'{LAUNCHER}-terminal-*'
SILENCED_CACHE = 'omnigent-silenced.json'
# Bridge directories are named for a digest; siblings like
# `codex-native/process-owners` are bookkeeping, not sessions.
BRIDGE_ID = re.compile(r'[0-9a-f]{32}\Z')
# Files naming the directory a bridge's harness is working in.
WORKSPACE_FIELDS = (('state.json', 'cwd'), ('bridge.json', 'workspace'))


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


def bridge_dirs():
    """Return (harness, directory) for every omnigent bridge on disk."""
    found = []
    for root in bridge_roots():
        for harness_dir in sorted(root.glob('*-native')):
            harness = harness_dir.name.removesuffix('-native')
            try:
                bridges = sorted(harness_dir.iterdir())
            except OSError:
                continue
            found += [
                (harness, b)
                for b in bridges
                if b.is_dir() and BRIDGE_ID.match(b.name)
            ]
    return found


def context_files():
    """Return (harness, path) for every bridge context file on disk."""
    found = []
    for harness, bridge in bridge_dirs():
        for name in CONTEXT_FILES:
            path = bridge / name
            if path.is_file():
                found.append((harness, path))
                break  # One record per bridge, raw preferred.
    return found


def read_pid_file(path):
    try:
        return path.read_text().strip()
    except OSError:
        return ''


def terminal_dirs():
    """Return the private directories omnigent gives its tmux servers."""
    found = []
    for parent in {Path(tempfile.gettempdir()), Path('/tmp')}:
        try:
            candidates = sorted(parent.glob(TERMINAL_GLOB))
        except OSError:
            continue
        for terminal in candidates:
            try:
                if terminal.stat().st_uid == os.getuid():
                    found.append(terminal)
            except OSError:
                continue
    return found


def bridge_workspace(bridge):
    """Return the directory the bridge's harness is working in."""
    for name, field in WORKSPACE_FIELDS:
        value = read_json(bridge / name).get(field)
        if isinstance(value, str) and value:
            return value
    return None


def bridge_for_cwd(cwd):
    """Return the bridge directory whose harness is working in cwd."""
    best, best_rank = None, None
    for _, bridge in bridge_dirs():
        try:
            modified = bridge.stat().st_mtime_ns
        except OSError:
            continue
        rank = (bool(cwd) and bridge_workspace(bridge) == cwd, modified)
        if best_rank is None or rank > best_rank:
            best, best_rank = bridge, rank
    return best


def inner_tmux_socket(bridge):
    """Return the tmux socket omnigent runs this bridge's harness on."""
    socket = read_json(bridge / TMUX_FILE).get('socket_path')
    if socket:
        return socket
    # Only claude-native writes tmux.json. For the rest, the runner that
    # owns the bridge also owns the terminal directory it created.
    owner = read_pid_file(bridge / OWNER_FILE)
    if not owner:
        return None
    matches = [
        terminal
        for terminal in terminal_dirs()
        if read_pid_file(terminal / OWNER_FILE) == owner
    ]
    if not matches:
        return None
    # One runner can own several terminals; the newest is this session's.
    newest = max(matches, key=lambda path: path.stat().st_mtime_ns)
    return str(newest / 'tmux.sock')


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


def hide_inner_status_bar(cwd=None):
    """Turn off the status bar of the tmux server omnigent runs this on.

    In server mode omnigent gives each harness a private tmux server with a
    status bar of its own, which stacks a second bar inside the footer's
    session. Only the server backing this pane's session is silenced, so
    omnigent sessions running outside the footer keep their own bar.
    """
    terminals = terminal_dirs()
    if not terminals:
        return
    cache_path = cache_dir() / SILENCED_CACHE
    silenced = set(read_json(cache_path).get('sockets') or [])
    if all(str(t / 'tmux.sock') in silenced for t in terminals):
        return
    bridge = bridge_for_cwd(cwd)
    socket = inner_tmux_socket(bridge) if bridge else None
    # tmux refuses a socket with no server rather than starting one, but
    # skipping the call keeps a dead path out of the cache.
    if not socket or socket in silenced or not Path(socket).exists():
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
        state = bridge_state(harness, record)
        # Several harnesses can be live at once, so prefer the one whose
        # own payload reports the directory this pane is sitting in.
        rank = (bool(cwd) and state.get('cwd') == cwd, modified)
        if best_rank is None or rank > best_rank:
            best, best_rank = state, rank
    if best:
        best['remote'] = remote_server()
    return best
