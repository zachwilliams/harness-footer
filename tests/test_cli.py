import contextlib
import io
import unittest

from harness_footer import cli


class CliTests(unittest.TestCase):
    def test_help_lists_every_command(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            cli.main(['--help'])
        for command in ('claude', 'codex', 'status', 'setup', 'claude-status'):
            self.assertIn(command, stdout.getvalue())

    def test_unknown_command_exits_with_usage(self):
        with self.assertRaises(SystemExit) as error:
            cli.main(['bogus'])
        self.assertIn('Unknown command: bogus', str(error.exception))

    def test_status_demo_prints_a_footer(self):
        with contextlib.redirect_stdout(io.StringIO()) as stdout:
            cli.main(['status', '--demo', '173000', '--plain'])
        self.assertIn('173K 17%', stdout.getvalue())


if __name__ == '__main__':
    unittest.main()
