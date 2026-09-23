import json
import tempfile
import unittest
from pathlib import Path

import footer


def claude_payload(tokens=173_000, session='one'):
    return {
        'session_id': session,
        'workspace': {'current_dir': '/tmp/project'},
        'model': {'display_name': 'Opus'},
        'context_window': {
            'context_window_size': 1_000_000,
            'current_usage': {
                'input_tokens': 3000,
                'cache_read_input_tokens': tokens - 3000,
                'cache_creation_input_tokens': 0,
                'output_tokens': 5000,
            },
        },
        'rate_limits': {
            'five_hour': {'used_percentage': 42},
            'seven_day': {'used_percentage': 57},
        },
    }


def render_plain(state, width=200, git=''):
    line = footer.render(state, '/tmp/project', git, footer.DEFAULTS, width)
    return footer.strip_styles(line)


class RenderTests(unittest.TestCase):
    def test_model_title_prefix_and_no_cumulative_tokens(self):
        cases = [
            ('claude', 'Opus (1M context)', 'claude-opus-5-5', 1_000_000,
             'Opus 5.5 (1m)'),
            ('codex', 'gpt-6-astra', None, 258_000, 'gpt-6-astra (258k)'),
        ]  # fmt: skip
        for app, model, model_id, window, title in cases:
            state = {
                'app': app,
                'model': model,
                'model_id': model_id,
                'window': window,
                'total': 42_000,
            }
            line = footer.render(
                state, '/tmp/project', '', footer.DEFAULTS, 250
            )
            self.assertIn(title, footer.strip_styles(line))
            self.assertNotIn('42K', footer.strip_styles(line))
            self.assertIn(',bold]' + app, line)
            self.assertIn(',nobold]', line)

    def test_cost_estimate_missing_and_zero_are_distinct(self):
        for cost in [0, 12.34, None]:
            data = claude_payload() | {'cost': {'total_cost_usd': cost}}
            state = footer.claude_state(data)
            label = footer.strip_styles(footer.cost_segment(state))
            expected = '' if cost is None else f'API est ${cost:.2f}'
            self.assertEqual(label, expected)
            self.assertIn(label, render_plain(state, width=80))
        codex_state = {'app': 'codex', 'total': 1_000_000}
        self.assertEqual(footer.cost_segment(codex_state), '')

    def test_both_labels_survive_narrow_widths(self):
        for app in ['claude', 'codex']:
            for width in [24, 40, 80, 120, 200]:
                state = footer.claude_state(claude_payload()) | {'app': app}
                line = footer.render(
                    state, '/tmp/project', '[feature]*', footer.DEFAULTS, width
                )
                self.assertTrue(
                    footer.strip_styles(line).startswith(' ' + app)
                )
                self.assertLessEqual(footer.display_width(line), width)

    def test_directory_and_branch_cannot_inject_formatting(self):
        line = footer.render(
            {}, '/tmp/#[bg=red]', '#(bad)\x1b', footer.DEFAULTS
        )
        self.assertNotIn('#(bad)', line)
        self.assertNotIn('#[bg=red]', line)
        self.assertNotIn('\x1b', line)


class ClaudeStateTests(unittest.TestCase):
    def test_enterprise_spend_limit_and_cost(self):
        data = claude_payload() | {
            'rate_limits': {'spend_limit': {'used_percentage': 125}},
            'cost': {'total_cost_usd': 5.25},
        }
        state = footer.claude_state(data)
        quota = footer.strip_styles(footer.quota_segment(state))
        self.assertIn('spend 125%', quota)
        self.assertEqual(state['estimated_cost_usd'], 5.25)

    def test_context_includes_cache_but_not_output(self):
        state = footer.claude_state(claude_payload())
        self.assertEqual(state['context'], 173_000)
        self.assertEqual(state['window'], 1_000_000)
        self.assertNotIn('total', state)
        quota = footer.strip_styles(footer.quota_segment(state))
        self.assertIn('5h 42% 7d 57%', quota)

    def test_compaction_startup_and_absent_quota(self):
        data = claude_payload()
        data['context_window']['current_usage'] = None
        data['context_window']['used_percentage'] = 50
        data['rate_limits'] = None
        state = footer.claude_state(data)
        self.assertIsNone(state['context'])
        context = footer.context_segment(state, footer.DEFAULTS)
        self.assertIn('awaiting', footer.strip_styles(context))
        self.assertEqual(footer.quota_segment(state), '')


class RolloutTests(unittest.TestCase):
    def test_incremental_rollout_and_compaction(self):
        token_count = {
            'type': 'event_msg',
            'payload': {
                'type': 'token_count',
                'info': {
                    'last_token_usage': {'total_tokens': 173_000},
                    'total_token_usage': {'total_tokens': 420_000},
                    'model_context_window': 1_000_000,
                },
            },
        }
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rollout.jsonl'
            cache = Path(temp) / 'cache'
            path.write_text(json.dumps(token_count) + '\n')
            state = footer.read_rollout(path, cache)
            self.assertEqual(state['context'], 173_000)
            self.assertEqual(state['total'], 420_000)

            with path.open('a') as rollout:
                rollout.write('{"type":"compacted"}\n{"type":')
            self.assertNotIn('context', footer.read_rollout(path, cache))

            with path.open('a') as rollout:
                rollout.write(
                    '"turn_context","payload":{"model":"new-model"}}\n'
                )
            self.assertEqual(
                footer.read_rollout(path, cache)['model'], 'new-model'
            )


if __name__ == '__main__':
    unittest.main()
