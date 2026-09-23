#!/usr/bin/env python3
"""Launch interactive Codex or Claude Code with the shared tmux footer."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from footer import (
    BACKGROUND,
    COLORS,
    PANE_APP_OPTION,
    PANE_TOKEN_OPTION,
    command_output,
    read_json,
)

ROOT = Path(__file__).resolve().parent
TMUX = shutil.which('tmux') or 'tmux'
TMUX_SERVER = 'harness-footer'
PYTHON = sys.executable
USAGE = 'Usage: harness-footer {codex|claude} [CLI arguments]'
INSIDE_TMUX_FLAG = '--inside'

SUBCOMMANDS = {
    'codex': frozenset({
        'a', 'agents', 'app', 'app-server', 'apply', 'archive', 'cloud',
        'completion', 'debug', 'delete', 'doctor', 'e', 'exec', 'exec-server',
        'features', 'help', 'login', 'logout', 'mcp', 'migrate-rollouts',
        'plugin', 'queue', 'remote-control', 'review', 'sandbox', 'unarchive',
        'update',
    }),
    'claude': frozenset({
        'agents', 'attach', 'auth', 'auto-mode', 'doctor', 'gateway', 'help',
        'import', 'install', 'kill', 'logs', 'mcp', 'plugin', 'plugins',
        'project', 'remote-control', 'respawn', 'rm', 'setup-token', 'stop',
        'ultrareview', 'update', 'upgrade',
    }),
}  # fmt: skip
NON_INTERACTIVE_SWITCHES = {
    'codex': frozenset({'-h', '--help', '--version', '-V'}),
    'claude': frozenset({
        '-h', '--help', '--version', '-v', '-p', '--print', '--bg',
        '--background', '--cloud', '--bare', '--safe-mode',
    }),
}  # fmt: skip
VALUE_FLAGS = {
    'codex': frozenset({
        '-a', '--add-dir', '--ask-for-approval', '-c', '-C', '--cd',
        '--config', '--disable', '--enable', '-i', '--image', '-m', '--model',
        '-p', '--profile', '--remote', '--remote-auth-token-env', '-s',
        '--sandbox',
    }),
    'claude': frozenset({
        '--agent', '--agents', '--append-system-prompt',
        '--append-system-prompt-file', '--autocompact', '--debug-file',
        '--effort', '--environment', '--fallback-model', '--input-format',
        '--max-budget-usd', '--model', '-n', '--name', '--output-format',
        '--permission-mode', '--permission-prompts', '--plugin-dir',
        '--plugin-url', '--remote-control-session-name-prefix',
        '--setting-sources', '--settings', '--system-prompt',
        '--system-prompt-file', '--system-prompt-snapshot',
    }),
}  # fmt: skip
# Flags whose following word may be an optional or variadic value, so it
# cannot be treated as a subcommand.
OPTIONAL_VALUE_FLAGS = {
    'codex': frozenset(),
    'claude': frozenset({
        '--add-dir', '--allowed-tools', '--allowedTools', '--betas', '-d',
        '--debug', '--disallowed-tools', '--disallowedTools', '--file',
        '--from-pr', '--mcp-config', '--prompt-suggestions', '-r',
        '--remote-control', '--resume', '--tools', '-w', '--worktree',
    }),
}  # fmt: skip
FORWARDED_ENVIRONMENT = (
    'ITERM_SESSION_ID',
    'ITERM_PROFILE',
    'TERM_PROGRAM',
    'TERM_PROGRAM_VERSION',
    'COLORTERM',
    'CODEX_HOME',
    'CLAUDE_CONFIG_DIR',
    'XDG_CACHE_HOME',
    'PATH',
)
CLAUDE_SETTING_SOURCES = frozenset({'user', 'project', 'local'})


def is_non_interactive(app, args):
    """Return True if args run a subcommand, print mode, help or version."""
    may_be_subcommand = True
    i = 0
    while i < len(args):
        arg = args[i]
        name = arg.partition('=')[0]
        if arg == '--':
            return False
        if name in NON_INTERACTIVE_SWITCHES[app]:
            return True
        # Covers combined short flags such as -pc.
        if app == 'claude' and arg.startswith('-p'):
            return True
        if arg in VALUE_FLAGS[app]:
            i += 2
            continue
        if name in OPTIONAL_VALUE_FLAGS[app]:
            may_be_subcommand = False
        elif not arg.startswith('-'):
            if may_be_subcommand and arg in SUBCOMMANDS[app]:
                return True
            may_be_subcommand = False
        i += 1
    return False


def tmux(*args):
    return subprocess.check_output([TMUX, *args], text=True).strip()


def configure_tmux(app, token):
    pane = os.environ['TMUX_PANE']
    session = tmux('display-message', '-p', '-t', pane, '#{session_id}')
    tmux('set-option', '-p', '-t', pane, PANE_APP_OPTION, app)
    tmux('set-option', '-p', '-t', pane, PANE_TOKEN_OPTION, token)
    footer = shlex.join([PYTHON, str(ROOT / 'footer.py')])
    command = (
        f'{footer} --socket #{{q:socket_path}} --pane #{{pane_id}}'
        ' --width #{client_width}'
    )
    options = {
        'status': 'on',
        'status-position': 'bottom',
        'status-interval': '2',
        'status-style': f'bg={BACKGROUND},fg={COLORS["base"]}',
        'status-format[0]': f'#({command})',
    }
    for key, value in options.items():
        tmux('set-option', '-t', session, key, value)


def merge(base, extra):
    """Recursively merge extra into base, as Claude merges settings files."""
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def option_value(args, index):
    """Return an option's value and how many arguments the option spans."""
    _, has_inline_value, inline_value = args[index].partition('=')
    if has_inline_value:
        return inline_value, 1
    if index + 1 < len(args):
        return args[index + 1], 2
    return None, 1


def load_settings_argument(value):
    if value.lstrip().startswith('{'):
        return json.loads(value)
    return json.loads(Path(value).expanduser().read_text())


def extract_settings_arguments(args):
    """Split args into remaining args, merged --settings, setting sources."""
    remaining, settings = [], {}
    sources = CLAUDE_SETTING_SOURCES
    i = 0
    while i < len(args):
        arg = args[i]
        name = arg.partition('=')[0]
        if arg == '--':
            remaining.extend(args[i:])
            break
        if name == '--settings':
            value, span = option_value(args, i)
            if value is not None:
                merge(settings, load_settings_argument(value))
                i += span
                continue
        elif name == '--setting-sources':
            value, _ = option_value(args, i)
            if value is not None:
                sources = frozenset(value.split(','))
        remaining.append(arg)
        i += 1
    return remaining, settings, sources


def configured_claude_settings(sources):
    config_dir = os.environ.get('CLAUDE_CONFIG_DIR') or Path.home() / '.claude'
    project_root = command_output(['git', 'rev-parse', '--show-toplevel'])
    project_dir = Path(project_root or os.getcwd()) / '.claude'
    paths = {
        'user': Path(config_dir) / 'settings.json',
        'project': project_dir / 'settings.json',
        'local': project_dir / 'settings.local.json',
    }
    settings = {}
    for source, path in paths.items():
        if source in sources:
            merge(settings, read_json(path))
    return settings


def claude_arguments(args, token):
    """Add the status-line bridge while keeping the caller's own settings.

    Claude accepts only one --settings value, so explicit settings are merged
    into the single override that installs the bridge.
    """
    remaining, explicit, sources = extract_settings_arguments(args)
    effective = merge(configured_claude_settings(sources), explicit)
    original = effective.get('statusLine') or {}
    bridge = [PYTHON, str(ROOT / 'claude_status.py'), '--token', token]
    if original.get('command'):
        bridge += ['--forward', original['command']]
    explicit['statusLine'] = {
        **original,
        'type': 'command',
        'command': shlex.join(bridge),
    }
    return ['--settings', json.dumps(explicit), *remaining]


def run_in_current_pane(app, executable, args):
    token = uuid.uuid4().hex
    if app == 'codex':
        app_args = ['--no-daemon', '-c', 'tui.status_line=[]', *args]
    else:
        app_args = claude_arguments(args, token)
    configure_tmux(app, token)
    os.execv(executable, [executable, *app_args])


def run_in_new_session(app, args):
    session = f'{app}-{uuid.uuid4().hex[:8]}'
    command = shlex.join(
        [PYTHON, str(ROOT / 'launch.py'), app, INSIDE_TMUX_FLAG, *args]
    )
    environment = []
    for key in FORWARDED_ENVIRONMENT:
        if key in os.environ:
            environment += ['-e', f'{key}={os.environ[key]}']
    server = ['-L', TMUX_SERVER, '-f', str(ROOT / 'tmux.conf')]
    new_session = ['new-session', '-s', session, '-c', os.getcwd()]
    os.execv(TMUX, [TMUX, *server, *new_session, *environment, command])


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(USAGE)
        return
    app, *args = sys.argv[1:]
    if app not in SUBCOMMANDS:
        raise SystemExit(USAGE)
    executable = shutil.which(app)
    if not executable:
        raise SystemExit(f'{app} is not on PATH')

    inside_tmux = args[:1] == [INSIDE_TMUX_FLAG]
    if inside_tmux:
        args = args[1:]
    has_terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if is_non_interactive(app, args) or not (inside_tmux or has_terminal):
        os.execv(executable, [executable, *args])
    if not shutil.which(TMUX):
        raise SystemExit(
            'tmux is required for the footer; install it and retry.'
        )
    if inside_tmux or os.environ.get('TMUX'):
        run_in_current_pane(app, executable, args)
    else:
        run_in_new_session(app, args)


if __name__ == '__main__':
    main()
