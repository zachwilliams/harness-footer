import json
import tempfile
import unittest
from pathlib import Path

from harness_footer.codex_usage import read_rollout

TOKEN_COUNT_EVENT = {
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


class RolloutTests(unittest.TestCase):
    def test_incremental_rollout_and_compaction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'rollout.jsonl'
            cache = Path(temp) / 'cache'
            path.write_text(json.dumps(TOKEN_COUNT_EVENT) + '\n')
            state = read_rollout(path, cache)
            self.assertEqual(state['context'], 173_000)
            self.assertEqual(state['total'], 420_000)

            with path.open('a') as rollout:
                rollout.write('{"type":"compacted"}\n{"type":')
            self.assertNotIn('context', read_rollout(path, cache))

            with path.open('a') as rollout:
                rollout.write('"turn_context","payload":{"model":"new"}}\n')
            self.assertEqual(read_rollout(path, cache)['model'], 'new')


if __name__ == '__main__':
    unittest.main()
