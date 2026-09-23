import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import claude_status
import footer
import launch


def payload(tokens=173000, session='one'):
    return {'session_id': session, 'workspace': {'current_dir': '/tmp/project'},
            'model': {'display_name': 'Opus'},
            'context_window': {'context_window_size': 1000000, 'current_usage': {
                'input_tokens': 3000, 'cache_read_input_tokens': tokens - 3000,
                'cache_creation_input_tokens': 0, 'output_tokens': 5000}},
            'rate_limits': {'five_hour': {'used_percentage': 42},
                            'seven_day': {'used_percentage': 57}}}


class FooterTests(unittest.TestCase):
    def test_model_title_prefix_and_no_cumulative_tokens(self):
        for app, model, model_id, window, title in [
            ('claude', 'Opus (1M context)', 'claude-opus-5-5', 1000000, 'Opus 5.5 (1m)'),
            ('codex', 'gpt-6-astra', None, 258000, 'gpt-6-astra (258k)'),
        ]:
            state = {'app': app, 'model': model, 'model_id': model_id, 'window': window, 'total': 42000}
            result = footer.render(state, '/tmp/project', '', footer.DEFAULTS, 250)
            self.assertIn(title, footer.plain(result))
            self.assertNotIn('42K', footer.plain(result))
            self.assertIn(',bold]' + app, result)
            self.assertIn(',nobold]', result)

    def test_cost_estimate_missing_and_zero_are_distinct(self):
        for cost in [0, 12.34, None]:
            data = payload() | {'cost': {'total_cost_usd': cost}}
            state = footer.claude_state(data)
            label = footer.plain(footer.cost_segment(state))
            self.assertEqual(label, '' if cost is None else f'API est ${cost:.2f}')
            self.assertIn(label, footer.plain(footer.render(state, '/tmp/project', '', footer.DEFAULTS, 80)))
        self.assertEqual(footer.cost_segment({'app': 'codex', 'total': 1000000}), '')

    def test_enterprise_spend_limit_and_cost(self):
        data = payload() | {'rate_limits': {'spend_limit': {'used_percentage': 125}},
                            'cost': {'total_cost_usd': 5.25}}
        state = footer.claude_state(data)
        self.assertIn('spend 125%', footer.plain(footer.quota_segment(state)))
        self.assertEqual(state['estimated_cost_usd'], 5.25)

    def test_claude_input_context_includes_cache_not_output(self):
        state = footer.claude_state(payload())
        self.assertEqual(state['context'], 173000)
        self.assertEqual(state['window'], 1000000)
        self.assertNotIn('total', state)  # Not a cumulative session count.
        self.assertIn('5h 42% 7d 57%', footer.plain(footer.quota_segment(state)))

    def test_compaction_startup_and_absent_quota(self):
        data = payload()
        data['context_window']['current_usage'] = None
        data['context_window']['used_percentage'] = 50
        data['rate_limits'] = None
        state = footer.claude_state(data)
        self.assertIsNone(state['context'])
        self.assertIn('awaiting', footer.plain(footer.context_segment(state, footer.DEFAULTS)))
        self.assertEqual(footer.quota_segment(state), '')

    def test_both_labels_survive_narrow_widths(self):
        for app in ['claude', 'codex']:
            for width in [24, 40, 80, 120, 200]:
                state = footer.claude_state(payload()) | {'app': app}
                result = footer.render(state, '/tmp/project', '[feature]*', footer.DEFAULTS, width)
                self.assertTrue(footer.plain(result).startswith(' ' + app))
                self.assertLessEqual(footer.cells(result), width)

    def test_directory_and_branch_cannot_inject_formatting(self):
        result = footer.render({}, '/tmp/#[bg=red]', '#(bad)\x1b', footer.DEFAULTS)
        self.assertNotIn('#(bad)', result)
        self.assertNotIn('#[bg=red]', result)
        self.assertNotIn('\x1b', result)

    def test_codex_incremental_rollout_and_compaction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rollout.jsonl'
            path.write_text(json.dumps({'type': 'event_msg', 'payload': {
                'type': 'token_count', 'info': {'last_token_usage': {'total_tokens': 173000},
                'total_token_usage': {'total_tokens': 420000}, 'model_context_window': 1000000}}}) + '\n')
            cache = Path(temp) / 'cache'
            state = footer.read_rollout(path, cache)
            self.assertEqual(state['context'], 173000)
            self.assertEqual(state['total'], 420000)
            with path.open('a') as out:
                out.write('{"type":"compacted"}\n{"type":')
            self.assertNotIn('context', footer.read_rollout(path, cache))
            with path.open('a') as out:
                out.write('"turn_context","payload":{"model":"new-model"}}\n')
            self.assertEqual(footer.read_rollout(path, cache)['model'], 'new-model')

    def test_cli_passthrough(self):
        for app, args in [('codex', ['exec', 'hi']), ('codex', ['-m', 'x', 'exec', 'hi']),
                          ('claude', ['-p', 'hello']), ('claude', ['--model', 'opus', '--print']),
                          ('claude', ['mcp', 'list']), ('claude', ['--version']),
                          ('claude', ['--background']), ('claude', ['hello', '-p'])]:
            self.assertTrue(launch.passthrough(app, args), (app, args))
        for app, args in [('codex', []), ('codex', ['resume']), ('codex', ['--model', 'exec']),
                          ('claude', []), ('claude', ['--continue']), ('claude', ['--resume', 'mcp']),
                          ('claude', ['--', '--print']), ('claude', ['--model', '--help'])]:
            self.assertFalse(launch.passthrough(app, args), (app, args))

    def test_explicit_settings_preserved_and_original_forwarded(self):
        original = {'permissions': {'defaultMode': 'plan'},
                    'statusLine': {'command': 'cat', 'padding': 2}}
        with patch('launch.read_json', return_value={}):
            args = launch.claude_args(['--settings', json.dumps(original), '--resume', 'abc'], 'a' * 32)
        settings = json.loads(args[1])
        self.assertEqual(settings['permissions'], original['permissions'])
        self.assertEqual(settings['statusLine']['padding'], 2)
        self.assertIn('--forward cat', settings['statusLine']['command'])
        self.assertEqual(args[2:], ['--resume', 'abc'])

    def test_bridge_isolates_launches_and_forwards_json(self):
        with tempfile.TemporaryDirectory() as temp:
            for token, session in [('a' * 32, 'one'), ('b' * 32, 'two')]:
                raw = json.dumps(payload(session=session))
                result = subprocess.run([sys.executable, str(launch.ROOT / 'claude_status.py'),
                                         '--token', token, '--forward', 'cat'],
                                        input=raw, text=True, capture_output=True, check=True,
                                        env=os.environ | {'XDG_CACHE_HOME': temp})
                self.assertEqual(json.loads(result.stdout)['session_id'], session)
                cache = Path(temp) / 'agent-tmux' / f'claude-{token}.json'
                self.assertEqual(json.loads(cache.read_text())['session_id'], session)
                self.assertEqual(cache.stat().st_mode & 0o777, 0o600)

    def test_bridge_can_hide_internal_status_without_losing_metrics(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(os.environ, {'XDG_CACHE_HOME': temp}), \
                patch.object(sys, 'argv', ['bridge', '--token', 'c' * 32, '--forward', 'cat']), \
                patch.object(sys, 'stdin', io.StringIO(json.dumps(payload()))), \
                patch('claude_status.settings', return_value={'claude_show_builtin_status': False}), \
                patch('claude_status.subprocess.run') as forward, contextlib.redirect_stdout(io.StringIO()) as out:
            claude_status.main()
            forward.assert_not_called()
            self.assertEqual(out.getvalue(), '')
            self.assertTrue((Path(temp) / 'agent-tmux' / ('claude-' + 'c' * 32 + '.json')).exists())


if __name__ == '__main__':
    unittest.main()
