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

import claude_status
from common import ROOT
from tests.fixtures import claude_payload


class ClaudeStatusTests(unittest.TestCase):
    def test_launches_are_isolated_and_json_is_forwarded(self):
        with tempfile.TemporaryDirectory() as temp:
            for token, session in [('a' * 32, 'one'), ('b' * 32, 'two')]:
                result = subprocess.run(
                    [
                        sys.executable,
                        str(ROOT / 'claude_status.py'),
                        '--token',
                        token,
                        '--forward',
                        'cat',
                    ],
                    input=json.dumps(claude_payload(session=session)),
                    text=True,
                    capture_output=True,
                    check=True,
                    env=os.environ | {'XDG_CACHE_HOME': temp},
                )
                forwarded = json.loads(result.stdout)
                self.assertEqual(forwarded['session_id'], session)
                cache = Path(temp) / 'harness-footer' / f'claude-{token}.json'
                cached = json.loads(cache.read_text())
                self.assertEqual(cached['session_id'], session)
                self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def test_hiding_builtin_status_still_caches_metrics(self):
        token = 'c' * 32
        settings = {'claude_show_builtin_status': False}
        argv = ['claude_status', '--token', token, '--forward', 'cat']
        stdin = io.StringIO(json.dumps(claude_payload()))
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, {'XDG_CACHE_HOME': temp}),
            patch.object(sys, 'argv', argv),
            patch.object(sys, 'stdin', stdin),
            patch('claude_status.load_settings', return_value=settings),
            patch('claude_status.subprocess.run') as forward,
            contextlib.redirect_stdout(io.StringIO()) as stdout,
        ):
            claude_status.main()
            forward.assert_not_called()
            self.assertEqual(stdout.getvalue(), '')
            cache = Path(temp) / 'harness-footer' / f'claude-{token}.json'
            self.assertTrue(cache.exists())


if __name__ == '__main__':
    unittest.main()
