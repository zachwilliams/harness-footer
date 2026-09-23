import json
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import install


class InstallTests(unittest.TestCase):
    def test_previous_project_name_is_migrated(self):
        with tempfile.TemporaryDirectory() as temp:
            rc = Path(temp) / '.zshrc'
            rc.write_text(
                f'{install.LEGACY_BEGIN_MARKER}\n'
                '. /old/activate.sh\n'
                f'{install.LEGACY_END_MARKER}\n'
            )
            install.connect_shell(Path(temp) / 'activate.sh', rc)
            self.assertNotIn('agent-tmux', rc.read_text())
            self.assertNotIn('/old/', rc.read_text())
            self.assertEqual(rc.read_text().count(install.BEGIN_MARKER), 1)

    def test_shell_connection_is_idempotent_and_backs_up_existing_config(self):
        with tempfile.TemporaryDirectory() as temp:
            rc = Path(temp) / '.zshrc'
            before = 'export EXISTING_SETTING=yes\n'
            rc.write_text(before)
            activation = Path(temp) / "a folder's script.sh"
            _, backup = install.connect_shell(activation, rc)
            self.assertEqual(backup.read_text(), before)
            self.assertTrue(rc.read_text().startswith(before))
            first = rc.read_text()
            _, backup_again = install.connect_shell(activation, rc)
            self.assertIsNone(backup_again)
            self.assertEqual(rc.read_text(), first)
            install.connect_shell(Path(temp) / 'moved.sh', rc)
            self.assertEqual(rc.read_text().count(install.BEGIN_MARKER), 1)
            self.assertNotIn('script.sh', rc.read_text())

    def test_portable_install_preserves_settings_and_forwards_arguments(self):
        with tempfile.TemporaryDirectory() as temp:
            # Exercise shell quoting, not just a simple installation path.
            prefix = Path(temp) / "an install's directory"
            target, wrapper, activation = install.install(prefix)
            custom = {
                'target_tokens': 180000,
                'claude_show_builtin_status': False,
            }
            (target / 'settings.json').write_text(json.dumps(custom))
            install.install(prefix)
            self.assertEqual(
                json.loads((target / 'settings.json').read_text()), custom
            )
            self.assertEqual(
                subprocess.check_output(
                    [str(wrapper), '--help'], text=True
                ).strip(),
                'Usage: harness-footer {codex|claude} [CLI arguments]',
            )
            fake_bin = Path(temp) / 'fake-bin'
            fake_bin.mkdir()
            for app in ('codex', 'claude'):
                path = fake_bin / app
                path.write_text('#!/bin/sh\nprintf "%s\\n" "$@"\n')
                path.chmod(0o755)
            env = os.environ | {
                'PATH': str(fake_bin) + os.pathsep + os.environ['PATH']
            }
            shells = [shutil.which(name) for name in ('bash', 'zsh')]
            for shell in filter(None, shells):
                for app in ('codex', 'claude'):
                    # Set PATH after shell startup; Zsh's .zshenv can reset it.
                    command = (
                        f'export PATH={shlex.quote(env["PATH"])}; '
                        f'. {shlex.quote(str(activation))}; '
                        f'{app} --version "two words"'
                    )
                    output = subprocess.check_output(
                        [shell, '-c', command], env=env, text=True
                    )
                    self.assertEqual(
                        output.splitlines(), ['--version', 'two words']
                    )
            self.assertFalse((target / 'test_footer.py').exists())


if __name__ == '__main__':
    unittest.main()
