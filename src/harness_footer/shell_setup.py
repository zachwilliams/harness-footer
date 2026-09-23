"""The `setup` command: route `claude` and `codex` through harness-footer."""

import argparse
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path

from harness_footer.common import PACKAGE_DIR, settings_path

REQUIRED_COMMANDS = ('tmux', 'git', 'lsof')
SUPPORTED_SHELLS = ('bash', 'zsh')
BEGIN_MARKER = '# >>> harness-footer >>>'
END_MARKER = '# <<< harness-footer <<<'
SHELL_BLOCK = f"""{BEGIN_MARKER}
claude() {{ harness-footer claude "$@"; }}
codex() {{ harness-footer codex "$@"; }}
{END_MARKER}
"""


def replace_marked_block(text, shell_rc):
    """Return text with SHELL_BLOCK added, or replacing an existing block."""
    if BEGIN_MARKER not in text and END_MARKER not in text:
        separator = '\n' if text and not text.endswith('\n') else ''
        return f'{text}{separator}\n{SHELL_BLOCK}'

    well_formed = (
        text.count(BEGIN_MARKER) == 1
        and text.count(END_MARKER) == 1
        and text.index(BEGIN_MARKER) < text.index(END_MARKER)
    )
    if not well_formed:
        raise SystemExit(
            f'Malformed harness-footer block in {shell_rc}; repair it first.'
        )
    start = text.index(BEGIN_MARKER)
    end = text.index(END_MARKER) + len(END_MARKER)
    if text.startswith('\n', end):
        end += 1
    return text[:start] + SHELL_BLOCK + text[end:]


def backup_path(shell_rc):
    stamp = datetime.now(UTC).strftime('%Y%m%dT%H%M%S%fZ')
    return shell_rc.with_name(f'{shell_rc.name}.harness-footer-{stamp}.bak')


def connect_shell(shell_rc):
    """Write SHELL_BLOCK to shell_rc; return (shell_rc, backup or None)."""
    shell_rc = Path(shell_rc).expanduser().resolve()
    current = shell_rc.read_text() if shell_rc.exists() else ''
    updated = replace_marked_block(current, shell_rc)
    if updated == current:
        return shell_rc, None

    backup = None
    shell_rc.parent.mkdir(parents=True, exist_ok=True)
    if shell_rc.exists():
        backup = backup_path(shell_rc)
        shutil.copy2(shell_rc, backup)
    shell_rc.write_text(updated)
    return shell_rc, backup


def create_settings():
    """Copy the example settings into place unless settings already exist."""
    path = settings_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(PACKAGE_DIR / 'settings.example.json', path)
    return path


def default_shell_rc(shell):
    if shell == 'zsh':
        return Path(os.environ.get('ZDOTDIR') or Path.home()) / '.zshrc'
    return Path.home() / '.bashrc'


def check_environment():
    if sys.platform not in ('darwin', 'linux'):
        raise SystemExit('harness-footer supports macOS and Linux (and WSL).')
    missing = [name for name in REQUIRED_COMMANDS if not shutil.which(name)]
    if missing:
        raise SystemExit('Install these first: ' + ', '.join(missing))


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog='harness-footer setup', description=__doc__
    )
    parser.add_argument(
        '--shell',
        choices=SUPPORTED_SHELLS,
        default=Path(os.environ.get('SHELL', '/bin/zsh')).name,
        help='Shell to configure (default: $SHELL)',
    )
    parser.add_argument(
        '--shell-rc', type=Path, help='Startup file to edit instead'
    )
    parser.add_argument(
        '--print',
        action='store_true',
        help='Print the shell block instead of editing a startup file',
    )
    args = parser.parse_args(argv)
    if args.shell not in SUPPORTED_SHELLS and not args.print:
        parser.error('Select --shell bash or --shell zsh')
    return args


def main(argv):
    args = parse_args(argv)
    if args.print:
        print(SHELL_BLOCK, end='')
        return
    check_environment()
    shell_rc, backup = connect_shell(
        args.shell_rc or default_shell_rc(args.shell)
    )
    print(f'Updated {shell_rc}')
    if backup:
        print(f'Backup: {backup}')
    print(f'Settings: {create_settings()}')
    if not shutil.which('harness-footer'):
        print(
            'harness-footer is not on your PATH yet; '
            'run `uv tool update-shell` to fix that.'
        )
    print('Open a new shell, then run claude or codex.')
