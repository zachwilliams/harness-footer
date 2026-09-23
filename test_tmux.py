"""Isolated real-tmux smoke test with fake CLIs; no model requests or user sessions."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import time

import launch


def main():
    with tempfile.TemporaryDirectory(prefix='harness-footer-test-') as temp:
        root = Path(temp)
        socket = str(root / 'tmux.sock')
        bins = root / 'bin'
        bins.mkdir()
        (root / 'claude').mkdir()
        (root / 'claude/settings.json').write_text('{}')
        for app in ('claude', 'codex'):
            path = bins / app
            path.write_text(f'#!{sys.executable}\n' + '''
import json, os, pathlib, subprocess, sys, time
args = sys.argv[1:]
pathlib.Path(os.environ['TEST_RESULT']).write_text(json.dumps(args))
if pathlib.Path(sys.argv[0]).name == 'claude':
    settings = json.loads(args[args.index('--settings') + 1])
    payload = {'model': {'display_name': 'Opus'}, 'workspace': {'current_dir': os.getcwd()},
               'context_window': {'context_window_size': 1000000, 'current_usage': {
                   'input_tokens': int(os.environ['TEST_TOKENS']), 'cache_read_input_tokens': 0,
                   'cache_creation_input_tokens': 0}}}
    subprocess.run(settings['statusLine']['command'], shell=True, input=json.dumps(payload),
                   text=True, check=True)
print('Fixture ready', flush=True)
time.sleep(60)
''')
            path.chmod(0o700)

        def tmux(*args):
            return subprocess.check_output([launch.TMUX, '-S', socket, *args], text=True).strip()

        try:
            panes = []
            for name, app, tokens in [('one', 'claude', 173000), ('two', 'claude', 42000),
                                      ('three', 'codex', 0)]:
                env = {'PATH': str(bins) + os.pathsep + os.environ['PATH'],
                       'XDG_CACHE_HOME': str(root / 'cache'), 'CLAUDE_CONFIG_DIR': str(root / 'claude'),
                       'TEST_RESULT': str(root / f'{name}.json'), 'TEST_TOKENS': str(tokens)}
                # Set PATH after tmux's command shell has run its startup files.
                command = shlex.join(['env', *[f'{key}={value}' for key, value in env.items()],
                                      sys.executable, str(launch.ROOT / 'launch.py'), app, '--inside'])
                env_args = [arg for key, value in env.items() for arg in ['-e', f'{key}={value}']]
                pane = tmux('-f', str(launch.ROOT / 'tmux.conf'), 'new-session', '-d', '-P',
                            '-F', '#{pane_id}', '-s', name, '-c', temp, '-x', '140', '-y', '30',
                            *env_args, command)
                panes.append((name, app, pane, tokens))
            deadline = time.monotonic() + 8
            while time.monotonic() < deadline:
                if len(list((root / 'cache/harness-footer').glob('claude-*.json'))) == 2 and (root / 'three.json').exists():
                    break
                time.sleep(0.1)
            for name, app, pane, tokens in panes:
                assert tmux('show-option', '-v', '-t', name, 'status-position') == 'bottom'
                assert str(launch.ROOT / 'footer.py') in tmux('show-option', '-v', '-t', name, 'status-format[0]')
                assert tmux('show-option', '-pv', '-t', pane, '@harness-footer-app') == app
                for width in [140, 80]:
                    tmux('resize-window', '-t', name, '-x', str(width), '-y', '30')
                    output = subprocess.check_output([
                        sys.executable, str(launch.ROOT / 'footer.py'), '--socket', socket,
                        '--pane', pane, '--width', str(width), '--plain'], text=True,
                        env=os.environ | {'XDG_CACHE_HOME': str(root / 'cache')})
                    assert output.startswith(' ' + app), output
                    if app == 'claude':
                        assert ('173K' if tokens == 173000 else '42K') in output, output
                    assert len(output.strip('\n')) <= width, output
            codex_args = json.loads((root / 'three.json').read_text())
            assert codex_args == ['--no-daemon', '-c', 'tui.status_line=[]'], codex_args
            # Sessions opened before the project rename retain their pane keys
            # and cached usage until the next Claude status update.
            pane = panes[0][2]
            token = tmux('show-option', '-pv', '-t', pane, '@harness-footer-token')
            tmux('set-option', '-p', '-t', pane, '@agent-footer-app', 'claude')
            tmux('set-option', '-p', '-t', pane, '@agent-footer-token', token)
            tmux('set-option', '-pu', '-t', pane, '@harness-footer-app')
            tmux('set-option', '-pu', '-t', pane, '@harness-footer-token')
            legacy = root / 'cache/agent-tmux'
            legacy.mkdir()
            (root / 'cache/harness-footer' / f'claude-{token}.json').rename(legacy / f'claude-{token}.json')
            output = subprocess.check_output([
                sys.executable, str(launch.ROOT / 'footer.py'), '--socket', socket,
                '--pane', pane, '--plain'], text=True,
                env=os.environ | {'XDG_CACHE_HOME': str(root / 'cache')})
            assert '173K' in output and output.startswith(' claude'), output
            print('PASS: isolated sessions, Codex flags, bottom status, 140/80 columns, legacy session compatibility')
        except Exception:
            for name, app, pane, tokens in panes:
                print(name, tmux('capture-pane', '-p', '-t', pane))
            raise
        finally:
            subprocess.run([launch.TMUX, '-S', socket, 'kill-server'], capture_output=True)


if __name__ == '__main__':
    main()
