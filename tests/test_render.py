import unittest

from harness_footer.claude_usage import claude_state
from harness_footer.common import DEFAULTS
from harness_footer.render import (
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
            # Without a quota the estimate is the only spend signal left.
            no_quota = state | {'rate_limits': {}}
            self.assertIn(label, render_plain(no_quota, width=80))
        self.assertEqual(
            cost_segment({'app': 'codex', 'total': 1_000_000}), ''
        )

    def test_quota_replaces_the_cost_estimate_at_every_width(self):
        data = claude_payload() | {'cost': {'total_cost_usd': 11.41}}
        state = claude_state(data)
        self.assertEqual(strip_styles(cost_segment(state)), 'API est $11.41')
        for width in [24, 40, 80, 120, 200, 250]:
            line = render_plain(state, width=width)
            self.assertNotIn('API est', line)
        # The same session shows it once the plan stops reporting a quota.
        self.assertIn('API est', render_plain(state | {'rate_limits': {}}))

    def test_only_the_window_closest_to_its_limit_gets_a_bar(self):
        def quota(five_hour, seven_day):
            data = claude_payload() | {
                'rate_limits': {
                    'five_hour': {'used_percentage': five_hour},
                    'seven_day': {'used_percentage': seven_day},
                }
            }
            line = render_plain(claude_state(data))
            return line[line.index('quota') :]

        self.assertTrue(quota(26, 3).startswith('quota 5h[███░░░░░░░] 26% -'))
        self.assertTrue(quota(3, 26).endswith('7d[███░░░░░░░] 26%'))
        # A tie keeps the bar on the first window rather than drawing two.
        self.assertEqual(quota(40, 40).count('['), 1)

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
