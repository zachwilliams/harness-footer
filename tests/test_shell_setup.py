import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from harness_footer import shell_setup
from tests.fixtures import isolated_environment


class ShellSetupTests(unittest.TestCase):
    def test_setup_is_idempotent_and_backs_up_existing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            rc = Path(temp) / '.zshrc'
            before = 'export EXISTING_SETTING=yes\n'
            rc.write_text(before)

            _, backup = shell_setup.connect_shell(rc)
            self.assertEqual(backup.read_text(), before)
            self.assertEqual(
                rc.read_text(), f'{before}\n{shell_setup.SHELL_BLOCK}'
            )

            _, second_backup = shell_setup.connect_shell(rc)
            self.assertIsNone(second_backup)
            self.assertEqual(rc.read_text().count(shell_setup.BEGIN_MARKER), 1)

    def test_an_old_block_is_replaced_in_place(self):
        with tempfile.TemporaryDirectory() as temp:
            rc = Path(temp) / '.zshrc'
            rc.write_text(
                'before\n'
                f'{shell_setup.BEGIN_MARKER}\n'
                '. /old/activate.sh\n'
                f'{shell_setup.END_MARKER}\n'
                'after\n'
            )
            shell_setup.connect_shell(rc)
            expected = f'before\n{shell_setup.SHELL_BLOCK}after\n'
            self.assertEqual(rc.read_text(), expected)

    def test_existing_settings_are_kept(self):
        with (
            tempfile.TemporaryDirectory() as temp,
            patch.dict(os.environ, isolated_environment(temp)),
        ):
            path = shell_setup.create_settings()
            self.assertIn('context_threshold', json.loads(path.read_text()))
            path.write_text('{"context_threshold": 180000}')
            shell_setup.create_settings()
            self.assertEqual(
                json.loads(path.read_text()), {'context_threshold': 180000}
            )

    def test_shell_functions_forward_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            fake_bin = Path(temp) / 'bin'
            fake_bin.mkdir()
            fake = fake_bin / 'harness-footer'
            fake.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
            fake.chmod(0o755)
            path = str(fake_bin) + os.pathsep + os.environ['PATH']
            block = shlex.quote(shell_setup.SHELL_BLOCK)
            for shell in filter(None, map(shutil.which, ('bash', 'zsh'))):
                for app in ('codex', 'claude'):
                    with self.subTest(shell=shell, app=app):
                        # Set PATH after startup; Zsh's .zshenv can reset it.
                        command = (
                            f'export PATH={shlex.quote(path)}; '
                            f'eval {block}; '
                            f'{app} --version "two words"'
                        )
                        output = subprocess.check_output(
                            [shell, '-c', command], text=True
                        )
                        self.assertEqual(
                            output.splitlines(),
                            [app, '--version', 'two words'],
                        )


if __name__ == '__main__':
    unittest.main()
