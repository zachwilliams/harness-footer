"""The `ai` command: a launcher for omnigent sessions on the remote server."""

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from harness_footer import launch
from harness_footer.common import PACKAGE_DIR

# Key, label, and the omnigent arguments that entry runs.
ENTRIES = (
    ('r', 'Resume a session', ['resume']),
    ('p', 'Polly (orchestrator)', ['polly']),
    ('d', 'Debby (brainstorm)', ['debby']),
    ('c', 'New Claude', ['claude']),
    ('x', 'New Codex', ['codex']),
    ('a', 'New Antigravity', ['agy']),
)
# Agents whose stopped sessions reopen through their own launcher.
RESUMABLE_AGENTS = frozenset({'polly', 'debby'})
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


def list_sessions(server):
    """Return the caller's sessions on server, or None if they can't be read.

    Only omnigent's own interpreter can build its auth headers, so the
    listing runs there.
    """
    executable = shutil.which('omni') or shutil.which('omnigent')
    interpreter = executable and launch.python_interpreter(executable)
    if not interpreter:
        return None
    helper = str(PACKAGE_DIR / 'omnigent_sessions.py')
    try:
        result = subprocess.run(
            [interpreter, '-P', helper, server],
            capture_output=True,
            text=True,
            timeout=60,
            check=True,
        )
        return json.loads(result.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def session_arguments(session, server):
    """Return the `omni` arguments that reopen a session.

    `omni resume` only reopens terminal-native sessions. A live agent
    session, such as polly running for the web UI, is joined with
    `attach`; resuming it would start a second runner for it.
    """
    session_id = session['id']
    if session['wrapper']:
        return ['resume', session_id, '--server', server]
    if session['runner_online']:
        return ['attach', session_id, '--server', server]
    if session['agent'] in RESUMABLE_AGENTS:
        agent = session['agent']
        return [agent, '--resume', session_id, '--server', server]
    return ['attach', session_id, '--server', server]


def age(timestamp, now=None):
    seconds = max(0, (now or time.time()) - timestamp)
    for unit, size in (('d', 86400), ('h', 3600), ('m', 60)):
        if seconds >= size:
            return f'{int(seconds // size)}{unit}'
    return 'now'


def session_line(number, session):
    live = '●' if session['runner_online'] else ' '
    kind = session['wrapper'].removesuffix('-ui') or session['agent']
    title = session['title'] or '(untitled)'
    return (
        f'  {number:>3}  {live} {kind:<18} {session["status"]:<8}'
        f' {age(session["updated_at"]):>4}  {title}'
    )


def pick_session(server):
    """Show the caller's sessions and return the `omni` arguments to run."""
    print('\n  Loading sessions…', flush=True)
    sessions = list_sessions(server)
    if sessions is None:
        # Fall back to omnigent's own picker.
        return ['resume', '--server', server]
    if not sessions:
        print('  No sessions on this server.')
        return None
    print(f'\n  sessions on {server}   ● live\n')
    for number, session in enumerate(sessions, 1):
        print(session_line(number, session))
    while True:
        try:
            answer = input('\n  number (Enter to quit): ').strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not answer or answer.lower() == 'q':
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(sessions):
            return session_arguments(sessions[int(answer) - 1], server)


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
    if key == 'r' and not args:
        omni_args = pick_session(server)
        if omni_args is None:
            return
    else:
        omni_args = omnigent_arguments(key, args, server)
    if omni_args is None:
        raise SystemExit(f'Unknown launcher key: {key}\n\nTry `ai --help`.')
    launch.main('omni', omni_args)
