"""Run an omnigent console script so its codex terminal keeps the editor.

Usage: python -P omnigent_bootstrap.py OMNIGENT_SCRIPT [ARGS...]

This runs under omnigent's own interpreter, so it must not import
harness_footer. Omnigent builds the codex TUI's environment from its host
daemon, which keeps only an allowlist, plus the few variables
`codex_terminal_env` copies from this process. EDITOR and VISUAL are in
neither, so Ctrl+G in codex fails. Adding them to that function's result
restores it; if omnigent renames the function, the launch runs unchanged.
"""

import os
import runpy
import sys

EDITOR_VARIABLES = ('EDITOR', 'VISUAL')


def keep_editor():
    try:
        from omnigent.harnesses.codex_native import main
    except Exception:
        return
    original = getattr(main, 'codex_terminal_env', None)
    if not callable(original):
        return

    def codex_terminal_env(*args, **kwargs):
        editor = {
            k: os.environ[k] for k in EDITOR_VARIABLES if k in os.environ
        }
        return editor | original(*args, **kwargs)

    main.codex_terminal_env = codex_terminal_env


if __name__ == '__main__':
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    keep_editor()
    runpy.run_path(script, run_name='__main__')
