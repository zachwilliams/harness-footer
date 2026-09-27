import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_footer import omnigent_usage
from tests.fixtures import claude_payload


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


if __name__ == '__main__':
    unittest.main()
