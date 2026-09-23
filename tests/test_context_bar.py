import unittest

from render import context_segment, strip_styles

THRESHOLD = {'context_threshold': 200_000}


def gauge(used, window=1_000_000, config=THRESHOLD):
    state = {'context': used, 'window': window}
    return strip_styles(context_segment(state, config))


def color(used, window=1_000_000):
    state = {'context': used, 'window': window}
    segment = context_segment(state, THRESHOLD)
    for name, code in [('red', '#f7768e'), ('yellow', '#e0af68')]:
        if code in segment:
            return name
    return 'green'


class ContextBarTests(unittest.TestCase):
    def test_bar_scales_linearly_across_the_window(self):
        self.assertEqual(gauge(0), 'ctx[░░░░│░░░░░░░░░░░░░░░░] 0 0%')
        self.assertEqual(gauge(500_000), 'ctx[████│██████░░░░░░░░░░] 500K 50%')
        self.assertEqual(
            gauge(1_000_000), 'ctx[████│████████████████] 1M 100%'
        )

    def test_threshold_marker_follows_the_setting(self):
        config = {'context_threshold': 500_000}
        self.assertEqual(
            gauge(0, config=config), 'ctx[░░░░░░░░░░│░░░░░░░░░░] 0 0%'
        )

    def test_no_marker_when_threshold_exceeds_window(self):
        self.assertEqual(
            gauge(0, window=128_000), 'ctx[░░░░░░░░░░░░░░░░░░░░] 0 0%'
        )

    def test_yellow_at_threshold_and_red_in_last_two_blocks(self):
        self.assertEqual(color(199_999), 'green')
        self.assertEqual(color(200_000), 'yellow')
        self.assertEqual(color(899_999), 'yellow')
        self.assertEqual(color(900_000), 'red')

    def test_small_window_turns_red_before_the_threshold(self):
        self.assertEqual(color(115_200, window=128_000), 'red')


if __name__ == '__main__':
    unittest.main()
