"""Find and incrementally read the rollout file of a running Codex CLI."""

import hashlib
import json
import shutil
from collections import deque
from pathlib import Path

from common import (
    cache_dir,
    command_output,
    non_negative_number,
    read_json,
    save_json,
)


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


def codex_state(pane_pid):
    """Return usage from the rollout of the codex process under pane_pid."""
    rollout = rollout_for_pid(find_codex_pid(pane_pid))
    return read_rollout(rollout, cache_dir()) if rollout else {}
