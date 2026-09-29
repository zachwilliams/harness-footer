"""The `ai` command: a launcher for omnigent sessions on the remote server."""

import os
import re
import sys
from pathlib import Path

from harness_footer import launch

# Key, label, and the omnigent arguments that entry runs.
ENTRIES = (
    ('r', 'Resume a session', ['resume']),
    ('p', 'Polly (orchestrator)', ['polly']),
    ('d', 'Debby (brainstorm)', ['debby']),
    ('c', 'New Claude', ['claude']),
    ('x', 'New Codex', ['codex']),
    ('a', 'New Antigravity', ['agy']),
)
QUIT_KEYS = frozenset({'q', '\x03', '\x04', '\x1b'})
USAGE = """\
usage: ai [key] [args]

  ai          open the launcher
{keys}
Extra args go to the harness, e.g. `ai c --resume`.
"""


def omnigent_server():
    """Return the `server:` from omnigent's own config, or None."""
    home = os.environ.get('OMNIGENT_HOME') or Path.home() / '.omnigent'
    try:
        text = (Path(home) / 'config.yaml').read_text()
    except OSError:
        return None
    match = re.search(r'^server:\s*["\']?([^"\'\s#]+)', text, re.MULTILINE)
    return match[1] if match else None


def omnigent_arguments(key, args, server):
    """Return the `omni` arguments for a launcher key and extra args."""
    for entry_key, _, command in ENTRIES:
        if entry_key == key:
            return [*command, '--server', server, *args]
    return None


def read_key():
    """Read one keypress without waiting for Enter."""
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        return os.read(fd, 1).decode(errors='ignore').lower()
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)


def choose(server):
    """Show the menu and return the chosen key, or None to quit."""
    print(f'\n  omnigent  {server}\n')
    for key, label, _ in ENTRIES:
        print(f'    {key}  {label}')
    print('\n    q  Quit\n')
    keys = {key for key, _, _ in ENTRIES}
    while True:
        key = read_key()
        if key in keys:
            return key
        if key in QUIT_KEYS:
            return None


def main(args):
    if args[:1] in (['-h'], ['--help']):
        keys = '\n'.join(f'  ai {k}        {label}' for k, label, _ in ENTRIES)
        print(USAGE.format(keys=keys), end='')
        return
    server = omnigent_server()
    if not server:
        raise SystemExit(
            'No omnigent server configured; set `server:` in '
            '~/.omnigent/config.yaml or run `omni login`.'
        )
    if args:
        key, args = args[0], args[1:]
    elif sys.stdin.isatty():
        key = choose(server)
        if key is None:
            return
    else:
        raise SystemExit('ai needs a terminal; pass a key such as `ai c`.')
    omni_args = omnigent_arguments(key, args, server)
    if omni_args is None:
        raise SystemExit(f'Unknown launcher key: {key}\n\nTry `ai --help`.')
    launch.main('omni', omni_args)
