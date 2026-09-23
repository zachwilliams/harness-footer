"""Smoke-test the footer on an isolated tmux server using fake agent CLIs.

Sends no model requests and never touches the user's own tmux sessions.
"""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from footer import (
    LEGACY_CACHE_NAME,
    LEGACY_PANE_APP_OPTION,
    LEGACY_PANE_TOKEN_OPTION,
    PANE_APP_OPTION,
    PANE_TOKEN_OPTION,
    ROOT,
)
from launch import INSIDE_TMUX_FLAG, TMUX

FAKE_CLI = """
import json, os, pathlib, subprocess, sys, time

args = sys.argv[1:]
pathlib.Path(os.environ['TEST_RESULT']).write_text(json.dumps(args))
if pathlib.Path(sys.argv[0]).name == 'claude':
    settings = json.loads(args[args.index('--settings') + 1])
    usage = {
        'input_tokens': int(os.environ['TEST_TOKENS']),
        'cache_read_input_tokens': 0,
        'cache_creation_input_tokens': 0,
    }
    payload = {
        'model': {'display_name': 'Opus'},
        'workspace': {'current_dir': os.getcwd()},
        'context_window': {
            'context_window_size': 1000000,
            'current_usage': usage,
        },
    }
    subprocess.run(
        settings['statusLine']['command'],
        shell=True,
        input=json.dumps(payload),
        text=True,
        check=True,
    )
print('Fixture ready', flush=True)
time.sleep(60)
"""
SESSIONS = [
    ('one', 'claude', 173_000),
    ('two', 'claude', 42_000),
    ('three', 'codex', 0),
]
EXPECTED_LABELS = {173_000: '173K', 42_000: '42K'}
KEY_PROBE = """
import os, select, sys, time, tty

tty.setraw(0)
received = b''
deadline = time.monotonic() + 3
while time.monotonic() < deadline:
    if select.select([0], [], [], 0.1)[0]:
        received += os.read(0, 1024)
open(sys.argv[1], 'wb').write(received)
"""


class IsolatedTmux:
    def __init__(self, root):
        self.root = root
        self.socket = str(root / 'tmux.sock')
        self.cache = root / 'cache'
        self.bin = root / 'bin'
        self.bin.mkdir()
        (root / 'claude').mkdir()
        (root / 'claude' / 'settings.json').write_text('{}')
        for app in ('claude', 'codex'):
            path = self.bin / app
            path.write_text(f'#!{sys.executable}\n{FAKE_CLI}')
            path.chmod(0o700)

    def tmux(self, *args):
        command = [TMUX, '-S', self.socket, *args]
        return subprocess.check_output(command, text=True).strip()

    def start(self, name, app, tokens):
        env = {
            'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
            'XDG_CACHE_HOME': str(self.cache),
            'CLAUDE_CONFIG_DIR': str(self.root / 'claude'),
            'TEST_RESULT': str(self.root / f'{name}.json'),
            'TEST_TOKENS': str(tokens),
        }
        assignments = [f'{key}={value}' for key, value in env.items()]
        launcher = [sys.executable, str(ROOT / 'launch.py'), app]
        # Set PATH after tmux's command shell has run its startup files.
        command = shlex.join(
            ['env', *assignments, *launcher, INSIDE_TMUX_FLAG]
        )
        env_args = [arg for pair in assignments for arg in ('-e', pair)]
        return self.tmux(
            '-f', str(ROOT / 'tmux.conf'),
            'new-session', '-d', '-P', '-F', '#{pane_id}',
            '-s', name, '-c', str(self.root), '-x', '140', '-y', '30',
            *env_args, command,
        )  # fmt: skip

    def render(self, pane, width=200):
        command = [
            sys.executable, str(ROOT / 'footer.py'),
            '--socket', self.socket, '--pane', pane,
            '--width', str(width), '--plain',
        ]  # fmt: skip
        env = os.environ | {'XDG_CACHE_HOME': str(self.cache)}
        return subprocess.check_output(command, text=True, env=env)

    def wait_until_ready(self, claude_count, codex_result, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            caches = list(
                (self.cache / 'harness-footer').glob('claude-*.json')
            )
            if len(caches) == claude_count and codex_result.exists():
                return
            time.sleep(0.1)

    def kill(self):
        command = [TMUX, '-S', self.socket, 'kill-server']
        subprocess.run(command, capture_output=True, check=False)


def check_session(server, name, app, pane, tokens):
    position = server.tmux('show-option', '-v', '-t', name, 'status-position')
    assert position == 'bottom', position
    status = server.tmux('show-option', '-v', '-t', name, 'status-format[0]')
    assert str(ROOT / 'footer.py') in status, status
    pane_app = server.tmux('show-option', '-pv', '-t', pane, PANE_APP_OPTION)
    assert pane_app == app, pane_app
    for width in [140, 80]:
        server.tmux('resize-window', '-t', name, '-x', str(width), '-y', '30')
        output = server.render(pane, width)
        assert output.startswith(' ' + app), output
        if app == 'claude':
            assert EXPECTED_LABELS[tokens] in output, output
        assert len(output.strip('\n')) <= width, output


def check_legacy_pane(server, pane):
    """Panes opened before the rename keep working with their old keys."""
    token = server.tmux('show-option', '-pv', '-t', pane, PANE_TOKEN_OPTION)
    server.tmux(
        'set-option', '-p', '-t', pane, LEGACY_PANE_APP_OPTION, 'claude'
    )
    server.tmux(
        'set-option', '-p', '-t', pane, LEGACY_PANE_TOKEN_OPTION, token
    )
    server.tmux('set-option', '-pu', '-t', pane, PANE_APP_OPTION)
    server.tmux('set-option', '-pu', '-t', pane, PANE_TOKEN_OPTION)
    legacy_cache = server.cache / LEGACY_CACHE_NAME
    legacy_cache.mkdir()
    cache_name = f'claude-{token}.json'
    (server.cache / 'harness-footer' / cache_name).rename(
        legacy_cache / cache_name
    )
    output = server.render(pane)
    assert '173K' in output and output.startswith(' claude'), output


def check_keys_reach_cli(server):
    """Shift+Enter and Ctrl+B must reach the CLI instead of tmux."""
    assert server.tmux('show-option', '-gv', 'prefix') == 'None'
    assert server.tmux('show-option', '-gv', 'mouse') == 'on'
    probe = server.root / 'key_probe.py'
    received = server.root / 'keys.bin'
    probe.write_text(KEY_PROBE)
    command = shlex.join([sys.executable, str(probe), str(received)])
    server.tmux('new-session', '-d', '-s', 'keys', command)
    time.sleep(0.5)
    server.tmux('send-keys', '-t', 'keys', 'S-Enter')
    deadline = time.monotonic() + 5
    while not received.exists() and time.monotonic() < deadline:
        time.sleep(0.1)
    assert received.read_bytes() == b'\x1b[13;2u', received.read_bytes()


def main():
    with tempfile.TemporaryDirectory(prefix='harness-footer-test-') as temp:
        server = IsolatedTmux(Path(temp))
        panes = []
        try:
            for name, app, tokens in SESSIONS:
                pane = server.start(name, app, tokens)
                panes.append((name, app, pane, tokens))
            codex_result = server.root / 'three.json'
            server.wait_until_ready(claude_count=2, codex_result=codex_result)

            for session in panes:
                check_session(server, *session)
            codex_args = json.loads(codex_result.read_text())
            expected_args = ['--no-daemon', '-c', 'tui.status_line=[]']
            assert codex_args == expected_args, codex_args
            check_legacy_pane(server, panes[0][2])
            check_keys_reach_cli(server)
            print(
                'PASS: isolated sessions, Codex flags, bottom status, '
                '140/80 columns, legacy session compatibility, Shift+Enter'
            )
        except Exception:
            for name, _, pane, _ in panes:
                print(name, server.tmux('capture-pane', '-p', '-t', pane))
            raise
        finally:
            server.kill()


if __name__ == '__main__':
    main()
