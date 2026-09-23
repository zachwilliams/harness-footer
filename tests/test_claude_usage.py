import unittest

from claude_usage import claude_state
from common import DEFAULTS
from render import context_segment, quota_segment, strip_styles
from tests.fixtures import claude_payload


class ClaudeStateTests(unittest.TestCase):
    def test_enterprise_spend_limit_and_cost(self):
        data = claude_payload() | {
            'rate_limits': {'spend_limit': {'used_percentage': 125}},
            'cost': {'total_cost_usd': 5.25},
        }
        state = claude_state(data)
        self.assertIn('spend 125%', strip_styles(quota_segment(state)))
        self.assertEqual(state['estimated_cost_usd'], 5.25)

    def test_context_includes_cache_but_not_output(self):
        state = claude_state(claude_payload())
        self.assertEqual(state['context'], 173_000)
        self.assertEqual(state['window'], 1_000_000)
        self.assertNotIn('total', state)
        self.assertIn('5h 42% 7d 57%', strip_styles(quota_segment(state)))

    def test_compaction_startup_and_absent_quota(self):
        data = claude_payload()
        data['context_window']['current_usage'] = None
        data['context_window']['used_percentage'] = 50
        data['rate_limits'] = None
        state = claude_state(data)
        self.assertIsNone(state['context'])
        context = strip_styles(context_segment(state, DEFAULTS))
        self.assertIn('awaiting', context)
        self.assertEqual(quota_segment(state), '')


if __name__ == '__main__':
    unittest.main()
