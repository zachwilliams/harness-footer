"""The `claude`, `codex` and `omnigent` commands: run a CLI inside tmux."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

from harness_footer.common import (
    PACKAGE_DIR,
    PANE_APP_OPTION,
    PANE_HARNESS_OPTION,
    PANE_TOKEN_OPTION,
    SELF_COMMAND,
    command_output,
    read_json,
)
from harness_footer.render import BACKGROUND, COLORS

TMUX = shutil.which('tmux') or 'tmux'
TMUX_SERVER = 'harness-footer'
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
    'omnigent': frozenset({
        'config', 'debug', 'diagnose', 'doctor', 'extensions', 'help', 'host',
        'import', 'integration', 'login',
    }),
}  # fmt: skip
# The harnesses omnigent can launch. They are subcommands but they open an
# interactive session, so they keep the footer rather than skipping it.
OMNIGENT_HARNESSES = frozenset({
    'agy', 'claude', 'codex', 'cursor', 'debby', 'goose', 'hermes', 'kimi',
    'kiro', 'opencode', 'pi', 'polly', 'qwen',
})  # fmt: skip
# Harnesses omnigent runs on its own tmux server. Wrapping one would nest
# tmux in tmux, and the footer's pane would hold omnigent rather than the
# harness, so let them through untouched.
OMNIGENT_OWN_TERMINAL = frozenset({'agy'})
# Omnigent installs two console scripts for the same CLI, so both names have
# to be wrapped or the short one silently bypasses the footer.
APP_ALIASES = {'omni': 'omnigent'}
NON_INTERACTIVE_SWITCHES = {
    'codex': frozenset({'-h', '--help', '--version', '-V'}),
    'claude': frozenset({
        '-h', '--help', '--version', '-v', '-p', '--print', '--bg',
        '--background', '--cloud', '--bare', '--safe-mode',
    }),
    'omnigent': frozenset({'-h', '--help', '--version'}),
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
    'omnigent': frozenset(),
}  # fmt: skip
# Flags whose following word may be an optional or variadic value, so it
# cannot be treated as a subcommand.
OPTIONAL_VALUE_FLAGS = {
    'codex': frozenset(),
    'omnigent': frozenset(),
    'claude': frozenset({
        '--add-dir', '--allowed-tools', '--allowedTools', '--betas', '-d',
        '--debug', '--disallowed-tools', '--disallowedTools', '--file',
        '--from-pr', '--mcp-config', '--prompt-suggestions', '-r',
        '--remote-control', '--resume', '--tools', '-w', '--worktree',
    }),
}  # fmt: skip
FORWARDED_ENVIRONMENT = (
    # Codex and Claude open an external editor from these, and a tmux server
    # started before they were exported would otherwise never see them.
    'EDITOR',
    'VISUAL',
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


def canonical_app(app):
    return APP_ALIASES.get(app, app)


def nested_harness(args):
    """Return the harness omnigent will launch, and its own arguments."""
    for i, arg in enumerate(args):
        if arg == '--':
            return None
        if not arg.startswith('-'):
            # omnigent's own options before the harness are all switches,
            # so the first bare word is the harness name.
            if arg in OMNIGENT_HARNESSES:
                return arg, args[i + 1 :]
            return None
    return None


def runs_own_terminal(app, args):
    """Return True if the CLI brings its own tmux, so the footer stays out."""
    if canonical_app(app) != 'omnigent':
        return False
    nested = nested_harness(args)
    return bool(nested) and nested[0] in OMNIGENT_OWN_TERMINAL


def is_non_interactive(app, args):
    """Return True if args run a subcommand, print mode, help or version."""
    app = canonical_app(app)
    if app == 'omnigent':
        nested = nested_harness(args)
        if nested:
            # `omnigent claude -p ...` is as non-interactive as `claude -p ...`
            # for the harnesses whose arguments the footer knows.
            harness, rest = nested
            if harness not in NON_INTERACTIVE_SWITCHES:
                return False
            return is_non_interactive(harness, rest)
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


def configure_tmux(app, token, harness=''):
    pane = os.environ['TMUX_PANE']
    session = tmux('display-message', '-p', '-t', pane, '#{session_id}')
    tmux('set-option', '-p', '-t', pane, PANE_APP_OPTION, app)
    tmux('set-option', '-p', '-t', pane, PANE_TOKEN_OPTION, token)
    tmux('set-option', '-p', '-t', pane, PANE_HARNESS_OPTION, harness)
    footer = shlex.join([*SELF_COMMAND, 'status'])
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
    bridge = [*SELF_COMMAND, 'claude-status', '--token', token]
    if original.get('command'):
        bridge += ['--forward', original['command']]
    explicit['statusLine'] = {
        **original,
        'type': 'command',
        'command': shlex.join(bridge),
    }
    return ['--settings', json.dumps(explicit), *remaining]


def python_interpreter(script):
    """Return the Python a console script runs under, from its shebang."""
    try:
        with open(script, 'rb') as file:
            first_line = file.readline().decode().strip()
    except (OSError, UnicodeDecodeError):
        return None
    interpreter = first_line.removeprefix('#!').strip()
    # Launcher shebangs such as `#!/bin/sh` or `#!/usr/bin/env python` are
    # left alone; only a direct interpreter path can run the bootstrap.
    if not first_line.startswith('#!') or ' ' in interpreter:
        return None
    if not Path(interpreter).name.startswith('python'):
        return None
    return interpreter


def omnigent_command(executable, args):
    """Return the command that runs omnigent with args."""
    nested = nested_harness(args)
    interpreter = python_interpreter(executable)
    if not nested or nested[0] != 'codex' or not interpreter:
        return [executable, *args]
    bootstrap = str(PACKAGE_DIR / 'omnigent_bootstrap.py')
    return [interpreter, '-P', bootstrap, executable, *args]


def run_in_current_pane(app, executable, args):
    token = uuid.uuid4().hex
    app = canonical_app(app)
    harness = ''
    if app == 'codex':
        command = [executable, '--no-daemon', '-c', 'tui.status_line=[]']
        command += args
    elif app == 'omnigent':
        # Omnigent installs its own statusLine wrapper and owns Claude's
        # single --settings value, so the footer reads its bridge instead.
        command = omnigent_command(executable, args)
        harness = (nested_harness(args) or ('',))[0]
    else:
        command = [executable, *claude_arguments(args, token)]
    configure_tmux(app, token, harness)
    os.execv(command[0], command)


def run_in_new_session(app, args):
    session = f'{app}-{uuid.uuid4().hex[:8]}'
    command = shlex.join([*SELF_COMMAND, app, INSIDE_TMUX_FLAG, *args])
    environment = []
    for key in FORWARDED_ENVIRONMENT:
        if key in os.environ:
            environment += ['-e', f'{key}={os.environ[key]}']
    server = ['-L', TMUX_SERVER, '-f', str(PACKAGE_DIR / 'tmux.conf')]
    new_session = ['new-session', '-s', session, '-c', os.getcwd()]
    os.execv(TMUX, [TMUX, *server, *new_session, *environment, command])


def main(app, args):
    executable = shutil.which(app)
    if not executable:
        raise SystemExit(f'{app} is not on PATH')

    inside_tmux = args[:1] == [INSIDE_TMUX_FLAG]
    if inside_tmux:
        args = args[1:]
    has_terminal = sys.stdin.isatty() and sys.stdout.isatty()
    if (
        is_non_interactive(app, args)
        or runs_own_terminal(app, args)
        or not (inside_tmux or has_terminal)
    ):
        os.execv(executable, [executable, *args])
    if not shutil.which(TMUX):
        raise SystemExit(
            'tmux is required for the footer; install it and retry.'
        )
    if inside_tmux or os.environ.get('TMUX'):
        run_in_current_pane(app, executable, args)
    else:
        run_in_new_session(app, args)
