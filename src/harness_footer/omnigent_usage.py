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

from harness_footer.claude_usage import claude_context_tokens, claude_state
from harness_footer.common import non_negative_number, read_json

LAUNCHER = 'omnigent'
# The raw statusLine capture comes first: it is the whole payload, so it
# still carries the rate limits that omnigent's normalized record drops.
CONTEXT_FILES = ('context_raw.json', 'context.json')


def bridge_roots():
    """Return the omnigent runtime roots belonging to the current user."""
    # Omnigent namespaces its scratch directory by numeric uid on POSIX, and
    # names other temp directories `omnigent-terminal-*`, so match exactly.
    name = f'{LAUNCHER}-{os.getuid()}'
    # Harnesses do not agree on where their bridge lives: claude-native uses
    # the temp root, antigravity-native uses the home directory.
    candidates = [Path(tempfile.gettempdir()) / name, Path('/tmp') / name]
    candidates.append(Path.home() / f'.{LAUNCHER}')
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
    return best
