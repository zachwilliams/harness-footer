import json
import unittest
from unittest.mock import patch

from harness_footer import launch


class LaunchTests(unittest.TestCase):
    def test_non_interactive_invocations_bypass_the_footer(self):
        cases = [
            ('codex', ['exec', 'hi']),
            ('codex', ['-m', 'x', 'exec', 'hi']),
            ('claude', ['-p', 'hello']),
            ('claude', ['--model', 'opus', '--print']),
            ('claude', ['mcp', 'list']),
            ('claude', ['--version']),
            ('claude', ['--background']),
            ('claude', ['hello', '-p']),
        ]
        for app, args in cases:
            self.assertTrue(launch.is_non_interactive(app, args), (app, args))

    def test_interactive_invocations_use_the_footer(self):
        cases = [
            ('codex', []),
            ('codex', ['resume']),
            ('codex', ['--model', 'exec']),
            ('claude', []),
            ('claude', ['--continue']),
            ('claude', ['--resume', 'mcp']),
            ('claude', ['--', '--print']),
            ('claude', ['--model', '--help']),
        ]
        for app, args in cases:
            self.assertFalse(launch.is_non_interactive(app, args), (app, args))

    def test_explicit_settings_preserved_and_original_forwarded(self):
        original = {
            'permissions': {'defaultMode': 'plan'},
            'statusLine': {'command': 'cat', 'padding': 2},
        }
        args = ['--settings', json.dumps(original), '--resume', 'abc']
        with patch('harness_footer.launch.read_json', return_value={}):
            result = launch.claude_arguments(args, 'a' * 32)
        settings = json.loads(result[1])
        self.assertEqual(settings['permissions'], original['permissions'])
        self.assertEqual(settings['statusLine']['padding'], 2)
        self.assertIn('--forward cat', settings['statusLine']['command'])
        self.assertEqual(result[2:], ['--resume', 'abc'])


if __name__ == '__main__':
    unittest.main()
