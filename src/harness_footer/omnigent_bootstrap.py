"""Run an omnigent console script so its harness terminals keep the editor.

Usage: python -P omnigent_bootstrap.py OMNIGENT_SCRIPT [ARGS...]

This runs under omnigent's own interpreter, so it must not import
harness_footer. Omnigent's harness terminals are started by runners under
its host daemon, and the CLI gives that daemon only an allowlisted
environment. EDITOR and VISUAL are not on it, so Ctrl+G fails in codex
(Claude quietly falls back to vi). Adding them to the daemon environment,
and naming them in the runner passthrough list, carries them through to
every terminal. The daemon is long-lived, so this only takes effect for a
daemon spawned by a wrapped launch. If omnigent renames the function, the
launch runs unchanged.
"""

import os
import runpy
import sys

EDITOR_VARIABLES = ('EDITOR', 'VISUAL')
PASSTHROUGH = 'OMNIGENT_RUNNER_ENV_PASSTHROUGH'


def keep_editor():
    editor = {k: os.environ[k] for k in EDITOR_VARIABLES if k in os.environ}
    if not editor:
        return
    try:
        from omnigent import cli
    except Exception:
        return
    original = getattr(cli, '_build_host_daemon_env', None)
    if not callable(original):
        return

    def build_host_daemon_env(*args, **kwargs):
        env = original(*args, **kwargs)
        names = [n for n in env.get(PASSTHROUGH, '').split(',') if n]
        names += [name for name in editor if name not in names]
        return env | editor | {PASSTHROUGH: ','.join(names)}

    cli._build_host_daemon_env = build_host_daemon_env


if __name__ == '__main__':
    script = sys.argv[1]
    sys.argv = sys.argv[1:]
    keep_editor()
    runpy.run_path(script, run_name='__main__')
