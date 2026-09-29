import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from harness_footer import launch, launcher

SERVER = 'https://omni.example.com'


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.home = tempfile.TemporaryDirectory()
        self.addCleanup(self.home.cleanup)
        patch = mock.patch.dict(os.environ, {'OMNIGENT_HOME': self.home.name})
        patch.start()
        self.addCleanup(patch.stop)

    def write_config(self, text):
        (Path(self.home.name) / 'config.yaml').write_text(text)

    def test_reads_server_from_omnigent_config(self):
        self.write_config(f'host:\n  name: x\nserver: {SERVER}\ntui: {{}}\n')
        self.assertEqual(launcher.omnigent_server(), SERVER)

    def test_ignores_nested_server_keys(self):
        self.write_config(f'host:\n  server: {SERVER}\n')
        self.assertIsNone(launcher.omnigent_server())

    def test_missing_config_has_no_server(self):
        self.assertIsNone(launcher.omnigent_server())

    def test_keys_map_to_omnigent_harnesses_on_the_server(self):
        cases = {
            'c': 'claude',
            'x': 'codex',
            'a': 'agy',
            'p': 'polly',
            'd': 'debby',
            'r': 'resume',
        }
        for key, command in cases.items():
            self.assertEqual(
                launcher.omnigent_arguments(key, [], SERVER),
                [command, '--server', SERVER],
            )

    def test_extra_args_follow_the_server(self):
        self.assertEqual(
            launcher.omnigent_arguments('c', ['--resume'], SERVER),
            ['claude', '--server', SERVER, '--resume'],
        )

    def test_key_launches_through_the_footer(self):
        self.write_config(f'server: {SERVER}\n')
        with mock.patch.object(launch, 'main') as run:
            launcher.main(['x'])
        run.assert_called_once_with('omni', ['codex', '--server', SERVER])

    def test_unknown_key_exits(self):
        self.write_config(f'server: {SERVER}\n')
        with self.assertRaises(SystemExit) as error:
            launcher.main(['z'])
        self.assertIn('Unknown launcher key: z', str(error.exception))

    def test_no_server_exits(self):
        with self.assertRaises(SystemExit) as error:
            launcher.main(['c'])
        self.assertIn('No omnigent server', str(error.exception))

    def test_resume_keeps_the_footer(self):
        args = launcher.omnigent_arguments('r', [], SERVER)
        self.assertFalse(launch.is_non_interactive('omni', args))


if __name__ == '__main__':
    unittest.main()
