import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_footer import omnigent_usage
from harness_footer.common import DEFAULTS
from harness_footer.render import render, strip_styles
from tests.fixtures import claude_payload, isolated_environment


def write_bridge(root, harness, bridge_id, name, record):
    """Create one omnigent bridge directory holding a context file."""
    path = Path(root, f'{harness}-native', bridge_id)
    path.mkdir(parents=True, exist_ok=True)
    (path / name).write_text(json.dumps(record))
    return path


class OmnigentUsageTests(unittest.TestCase):
    def state(self, root, cwd=None):
        with patch.object(
            omnigent_usage, 'bridge_roots', return_value=[Path(root)]
        ):
            return omnigent_usage.omnigent_state(cwd)

    def test_raw_capture_keeps_rate_limits_and_names_the_launcher(self):
        with tempfile.TemporaryDirectory() as root:
            write_bridge(
                root, 'claude', 'a' * 32, 'context_raw.json', claude_payload()
            )
            state = self.state(root)
        self.assertEqual(state['app'], 'claude')
        self.assertEqual(state['launcher'], 'omnigent')
        self.assertEqual(state['context'], 173_000)
        self.assertEqual(state['window'], 1_000_000)
        # Only the raw payload carries these through omnigent's bridge.
        self.assertEqual(state['rate_limits']['primary']['used_percent'], 42)

    def test_normalized_record_is_read_when_no_raw_capture_exists(self):
        record = {
            'context_window_size': 200_000,
            'current_usage': {'input_tokens': 50_000},
            'total_cost_usd': 1.25,
            'model': 'claude-opus-5',
        }
        with tempfile.TemporaryDirectory() as root:
            write_bridge(root, 'cursor', 'b' * 32, 'context.json', record)
            state = self.state(root)
        self.assertEqual(state['app'], 'cursor')
        self.assertEqual(state['launcher'], 'omnigent')
        self.assertEqual(state['context'], 50_000)
        self.assertEqual(state['window'], 200_000)
        self.assertEqual(state['estimated_cost_usd'], 1.25)

    def test_the_bridge_working_in_this_directory_wins(self):
        with tempfile.TemporaryDirectory() as root:
            here = claude_payload(tokens=10_000)
            here['workspace']['current_dir'] = '/tmp/mine'
            write_bridge(root, 'claude', 'c' * 32, 'context_raw.json', here)
            # Written later, so mtime alone would pick this one instead.
            write_bridge(
                root, 'claude', 'd' * 32, 'context_raw.json', claude_payload()
            )
            state = self.state(root, cwd='/tmp/mine')
        self.assertEqual(state['cwd'], '/tmp/mine')
        self.assertEqual(state['context'], 10_000)

    def test_no_bridge_directory_reports_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            self.assertEqual(self.state(root), {})

    def test_remote_sessions_are_marked_in_the_harness_name(self):
        state = {'app': 'claude', 'launcher': 'omnigent'}
        local = strip_styles(render(state, '/tmp/p', '', DEFAULTS))
        remote = strip_styles(
            render(
                state | {'remote': 'omni.example.com'}, '/tmp/p', '', DEFAULTS
            )
        )
        self.assertIn('omnigent:claude', local)
        self.assertNotIn('↗', local)
        self.assertIn('omnigent↗:claude', remote)

    def test_remote_server_needs_a_live_server_daemon(self):
        cases = [
            (
                {
                    'mode': 'server',
                    'server_url': 'https://o.example.com',
                    'pid': os.getpid(),
                },
                'o.example.com',
            ),
            (
                {
                    'mode': 'local',
                    'server_url': 'https://o.example.com',
                    'pid': os.getpid(),
                },
                None,
            ),
            # A recorded daemon that is no longer running means no server.
            (
                {
                    'mode': 'server',
                    'server_url': 'https://o.example.com',
                    'pid': 2**30,
                },
                None,
            ),
            ({'mode': 'server', 'pid': os.getpid()}, None),
        ]
        for record, expected in cases:
            with tempfile.TemporaryDirectory() as home:
                daemons = Path(home, 'daemons')
                daemons.mkdir()
                (daemons / 'host.json').write_text(json.dumps(record))
                with patch.object(
                    omnigent_usage, 'omnigent_home', return_value=Path(home)
                ):
                    self.assertEqual(
                        omnigent_usage.remote_server(), expected, record
                    )

    def test_bridge_roots_match_the_uid_directory_only(self):
        with tempfile.TemporaryDirectory() as parent:
            mine = Path(parent, f'omnigent-{os.getuid()}')
            mine.mkdir()
            # Omnigent puts unrelated scratch directories here too.
            Path(parent, 'omnigent-terminal-abc123').mkdir()
            with patch.object(
                omnigent_usage.tempfile, 'gettempdir', return_value=parent
            ):
                roots = omnigent_usage.bridge_roots()
        self.assertIn(mine, roots)
        self.assertNotIn(Path(parent, 'omnigent-terminal-abc123'), roots)


@unittest.skipUnless(shutil.which('tmux'), 'tmux is not installed')
class InnerStatusBarTests(unittest.TestCase):
    """Omnigent gives each harness its own tmux server and status bar."""

    def start_server(self, socket):
        run = ['tmux', '-S', socket, '-f', os.devnull]
        subprocess.run(
            [*run, 'new-session', '-d', 'sleep 60'],
            check=True,
            capture_output=True,
        )
        self.addCleanup(
            subprocess.run, [*run, 'kill-server'], capture_output=True
        )
        subprocess.run(
            [*run, 'set-option', '-g', 'status', 'on'],
            check=True,
            capture_output=True,
        )

    def status(self, socket):
        result = subprocess.run(
            ['tmux', '-S', socket, 'show-options', '-g', 'status'],
            check=True,
            capture_output=True,
            text=True,
        )
        return result.stdout.strip()

    def test_only_the_recorded_tmux_server_is_silenced(self):
        with tempfile.TemporaryDirectory() as temp:
            ours = str(Path(temp, 'ours.sock'))
            theirs = str(Path(temp, 'theirs.sock'))
            self.start_server(ours)
            self.start_server(theirs)
            bridge = Path(temp, 'bridge')
            bridge.mkdir()
            (bridge / 'tmux.json').write_text(
                json.dumps({'socket_path': ours})
            )
            with patch.dict(os.environ, isolated_environment(temp)):
                omnigent_usage.hide_inner_status_bar(
                    {'bridge_dir': str(bridge)}
                )
            self.assertEqual(self.status(ours), 'status off')
            # An omnigent session running outside the footer keeps its bar.
            self.assertEqual(self.status(theirs), 'status on')

    def test_a_bridge_with_no_tmux_server_is_left_alone(self):
        with tempfile.TemporaryDirectory() as temp:
            bridge = Path(temp, 'bridge')
            bridge.mkdir()
            with patch.dict(os.environ, isolated_environment(temp)):
                omnigent_usage.hide_inner_status_bar(
                    {'bridge_dir': str(bridge)}
                )
                omnigent_usage.hide_inner_status_bar({})


if __name__ == '__main__':
    unittest.main()
