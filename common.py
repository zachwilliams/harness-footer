"""Settings, cache locations and small helpers shared by every script."""

import json
import math
import os
import re
import subprocess
import tempfile
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
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR

PANE_APP_OPTION = '@harness-footer-app'
PANE_TOKEN_OPTION = '@harness-footer-token'
# Panes and caches created before the project was renamed.
LEGACY_PANE_APP_OPTION = '@agent-footer-app'
LEGACY_PANE_TOKEN_OPTION = '@agent-footer-token'
LEGACY_CACHE_NAME = 'agent-tmux'


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
