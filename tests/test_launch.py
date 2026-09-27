import json
import unittest
from unittest.mock import patch

from harness_footer import launch, shell_setup


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
            ('omnigent', ['doctor']),
            ('omnigent', ['--version']),
            ('omnigent', ['claude', '-p', 'hello']),
            ('omnigent', ['--debug', 'claude', '--print']),
            ('omnigent', ['codex', 'exec', 'hi']),
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
            ('omnigent', []),
            ('omnigent', ['claude']),
            ('omnigent', ['polly']),
            ('omnigent', ['--debug', 'codex', '--model', 'x']),
            ('omnigent', ['resume']),
            ('omnigent', ['--', 'doctor']),
            ('omnigent', ['agy']),
        ]
        for app, args in cases:
            self.assertFalse(launch.is_non_interactive(app, args), (app, args))

    def test_omni_is_treated_as_omnigent(self):
        # Omnigent ships `omni` and `omnigent` as separate console scripts;
        # wrapping only one lets the other bypass the footer entirely.
        self.assertEqual(launch.canonical_app('omni'), 'omnigent')
        self.assertEqual(launch.canonical_app('omnigent'), 'omnigent')
        self.assertEqual(launch.canonical_app('claude'), 'claude')
        self.assertTrue(launch.is_non_interactive('omni', ['doctor']))
        self.assertTrue(launch.is_non_interactive('omni', ['claude', '-p']))
        self.assertFalse(launch.is_non_interactive('omni', ['claude']))
        self.assertTrue(launch.runs_own_terminal('omni', ['agy']))
        self.assertIn('omni', shell_setup.SHELL_BLOCK)

    def test_agy_keeps_its_own_terminal(self):
        # agy is interactive, but omnigent runs it on its own tmux server,
        # so the footer must not wrap it a second time.
        own = [
            ['agy'],
            ['agy', '--model', 'gemini'],
            ['--debug', 'agy'],
            ['agy', '-r', 'conv_x'],
        ]
        for args in own:
            self.assertTrue(launch.runs_own_terminal('omnigent', args), args)
        others = [['claude'], ['polly'], ['codex'], [], ['--', 'agy']]
        for args in others:
            self.assertFalse(launch.runs_own_terminal('omnigent', args), args)
        self.assertFalse(launch.runs_own_terminal('claude', ['agy']))

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
