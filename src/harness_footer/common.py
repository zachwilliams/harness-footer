"""Settings, file locations and small helpers shared by every command."""

import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
# -P stops the working directory from shadowing standard library modules.
SELF_COMMAND = (sys.executable, '-P', '-m', 'harness_footer')
DEFAULTS = {
    'context_threshold': 200_000,
    'claude_show_builtin_status': True,
}
MINUTES_PER_HOUR = 60
MINUTES_PER_DAY = 24 * MINUTES_PER_HOUR

PANE_APP_OPTION = '@harness-footer-app'
PANE_TOKEN_OPTION = '@harness-footer-token'


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


def config_dir():
    base = os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config'
    return Path(base) / 'harness-footer'


def settings_path():
    return config_dir() / 'settings.json'


def load_settings():
    result = DEFAULTS | read_json(settings_path())
    result['context_threshold'] = int(result['context_threshold'])
    if result['context_threshold'] <= 0:
        raise ValueError('context_threshold must be a positive token count')
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
