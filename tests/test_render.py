import unittest

from claude_usage import claude_state
from common import DEFAULTS
from render import (
    cost_segment,
    display_width,
    render,
    strip_styles,
)
from tests.fixtures import claude_payload


def render_plain(state, width=200, git=''):
    return strip_styles(render(state, '/tmp/project', git, DEFAULTS, width))


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
            line = render(state, '/tmp/project', '', DEFAULTS, 250)
            self.assertIn(title, strip_styles(line))
            self.assertNotIn('42K', strip_styles(line))
            self.assertIn(',bold]' + app, line)
            self.assertIn(',nobold]', line)

    def test_cost_estimate_missing_and_zero_are_distinct(self):
        for cost in [0, 12.34, None]:
            data = claude_payload() | {'cost': {'total_cost_usd': cost}}
            state = claude_state(data)
            label = strip_styles(cost_segment(state))
            expected = '' if cost is None else f'API est ${cost:.2f}'
            self.assertEqual(label, expected)
            self.assertIn(label, render_plain(state, width=80))
        self.assertEqual(
            cost_segment({'app': 'codex', 'total': 1_000_000}), ''
        )

    def test_both_labels_survive_narrow_widths(self):
        for app in ['claude', 'codex']:
            for width in [24, 40, 80, 120, 200]:
                state = claude_state(claude_payload()) | {'app': app}
                line = render(
                    state, '/tmp/project', '[feature]*', DEFAULTS, width
                )
                self.assertTrue(strip_styles(line).startswith(' ' + app))
                self.assertLessEqual(display_width(line), width)

    def test_directory_and_branch_cannot_inject_formatting(self):
        line = render({}, '/tmp/#[bg=red]', '#(bad)\x1b', DEFAULTS)
        self.assertNotIn('#(bad)', line)
        self.assertNotIn('#[bg=red]', line)
        self.assertNotIn('\x1b', line)


if __name__ == '__main__':
    unittest.main()
