"""Smoke-test the footer on an isolated tmux server using fake agent CLIs.

Sends no model requests and never touches the user's own tmux sessions.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from common import (
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
    ('legacy', 'claude', 173_000),
]
LEGACY_SESSION = 'legacy'
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


@unittest.skipUnless(shutil.which(TMUX), 'tmux is not installed')
class TmuxSmokeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='harness-footer-test-')
        cls.server = IsolatedTmux(Path(cls.temp.name))
        cls.panes = {
            name: (app, cls.server.start(name, app, tokens), tokens)
            for name, app, tokens in SESSIONS
        }
        cls.codex_result = cls.server.root / 'three.json'
        claude_count = sum(app == 'claude' for _, app, _ in SESSIONS)
        cls.server.wait_until_ready(claude_count, cls.codex_result)

    @classmethod
    def tearDownClass(cls):
        cls.server.kill()
        cls.temp.cleanup()

    def tmux(self, *args):
        return self.server.tmux(*args)

    def test_each_session_gets_its_own_footer(self):
        for name, (app, pane, tokens) in self.panes.items():
            if name == LEGACY_SESSION:
                continue
            with self.subTest(session=name):
                status = self.tmux('show-option', '-v', '-t', name, 'status')
                position = self.tmux(
                    'show-option', '-v', '-t', name, 'status-position'
                )
                status_format = self.tmux(
                    'show-option', '-v', '-t', name, 'status-format[0]'
                )
                pane_app = self.tmux(
                    'show-option', '-pv', '-t', pane, PANE_APP_OPTION
                )
                self.assertEqual(status, 'on')
                self.assertEqual(position, 'bottom')
                self.assertIn(str(ROOT / 'footer.py'), status_format)
                self.assertEqual(pane_app, app)
                self.check_layout(name, app, pane, tokens)

    def check_layout(self, name, app, pane, tokens):
        for width in [140, 80]:
            self.tmux(
                'resize-window', '-t', name, '-x', str(width), '-y', '30'
            )
            output = self.server.render(pane, width)
            self.assertTrue(output.startswith(' ' + app), output)
            if app == 'claude':
                self.assertIn(EXPECTED_LABELS[tokens], output)
            self.assertLessEqual(len(output.strip('\n')), width, output)

    def test_codex_is_launched_without_its_own_status_line(self):
        codex_args = json.loads(self.codex_result.read_text())
        expected = ['--no-daemon', '-c', 'tui.status_line=[]']
        self.assertEqual(codex_args, expected)

    def test_panes_from_before_the_rename_keep_working(self):
        _, pane, _ = self.panes[LEGACY_SESSION]
        token = self.tmux('show-option', '-pv', '-t', pane, PANE_TOKEN_OPTION)
        for legacy, current, value in [
            (LEGACY_PANE_APP_OPTION, PANE_APP_OPTION, 'claude'),
            (LEGACY_PANE_TOKEN_OPTION, PANE_TOKEN_OPTION, token),
        ]:
            self.tmux('set-option', '-p', '-t', pane, legacy, value)
            self.tmux('set-option', '-pu', '-t', pane, current)
        legacy_cache = self.server.cache / LEGACY_CACHE_NAME
        legacy_cache.mkdir()
        cache_name = f'claude-{token}.json'
        current_cache = self.server.cache / 'harness-footer' / cache_name
        current_cache.rename(legacy_cache / cache_name)

        output = self.server.render(pane)
        self.assertTrue(output.startswith(' claude'), output)
        self.assertIn('173K', output)

    def test_keys_reach_the_cli_instead_of_tmux(self):
        self.assertEqual(self.tmux('show-option', '-gv', 'prefix'), 'None')
        self.assertEqual(self.tmux('show-option', '-gv', 'mouse'), 'on')
        probe = self.server.root / 'key_probe.py'
        received = self.server.root / 'keys.bin'
        probe.write_text(KEY_PROBE)
        command = shlex.join([sys.executable, str(probe), str(received)])
        self.tmux('new-session', '-d', '-s', 'keys', command)
        time.sleep(0.5)
        self.tmux('send-keys', '-t', 'keys', 'S-Enter')
        deadline = time.monotonic() + 5
        while not received.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        self.assertEqual(received.read_bytes(), b'\x1b[13;2u')


if __name__ == '__main__':
    unittest.main()
