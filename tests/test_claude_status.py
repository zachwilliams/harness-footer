import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_footer import claude_status
from harness_footer.common import SELF_COMMAND
from tests.fixtures import claude_payload, isolated_environment


class ClaudeStatusTests(unittest.TestCase):
    def test_launches_are_isolated_and_json_is_forwarded(self):
        with tempfile.TemporaryDirectory() as temp:
            for token, session in [('a' * 32, 'one'), ('b' * 32, 'two')]:
                command = [*SELF_COMMAND, 'claude-status', '--token', token]
                result = subprocess.run(
                    [*command, '--forward', 'cat'],
                    input=json.dumps(claude_payload(session=session)),
                    text=True,
                    capture_output=True,
                    check=True,
                    env=os.environ | isolated_environment(temp),
                )
                forwarded = json.loads(result.stdout)
                self.assertEqual(forwarded['session_id'], session)
                cache = Path(
                    temp, 'cache', 'harness-footer', f'claude-{token}.json'
                )
                cached = json.loads(cache.read_text())
                self.assertEqual(cached['session_id'], session)
                self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def test_hiding_builtin_status_still_caches_metrics(self):
        token = 'c' * 32
        settings = {'claude_show_builtin_status': False}
        argv = ['--token', token, '--forward', 'cat']
        stdin = io.StringIO(json.dumps(claude_payload()))
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, isolated_environment(temp)),
            patch.object(sys, 'stdin', stdin),
            patch(
                'harness_footer.claude_status.load_settings',
                return_value=settings,
            ),
            patch('harness_footer.claude_status.subprocess.run') as forward,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            claude_status.main(argv)
            forward.assert_not_called()
            self.assertEqual(stdout.getvalue(), '')
            cache = Path(
                temp, 'cache', 'harness-footer', f'claude-{token}.json'
            )
            self.assertTrue(cache.exists())


if __name__ == '__main__':
    unittest.main()
