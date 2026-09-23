#!/usr/bin/env python3
"""Launch interactive Codex or Claude Code with the shared tmux footer."""
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import uuid

from footer import read_json

ROOT = Path(__file__).resolve().parent
TMUX = shutil.which('tmux') or 'tmux'
PYTHON = sys.executable
SUBCOMMANDS = {
    'codex': set('agents exec e review login logout mcp plugin app-server remote-control app '
                 'completion update doctor sandbox debug apply a queue archive delete migrate-rollouts '
                 'unarchive cloud exec-server features help'.split()),
    'claude': set('agents attach auth auto-mode doctor gateway import install logs mcp plugin plugins '
                  'project respawn rm setup-token stop kill ultrareview update upgrade help '
                  'remote-control'.split()),
}
VALUE_FLAGS = {
    'codex': set('-c --config -m --model -p --profile -s --sandbox -a --ask-for-approval '
                 '-C --cd -i --image --enable --disable --remote --remote-auth-token-env --add-dir'.split()),
    'claude': set('--agent --agents --append-system-prompt --autocompact --debug-file --effort '
                  '--environment --fallback-model --input-format --max-budget-usd --model -n --name '
                  '--output-format --permission-mode --permission-prompts --plugin-dir --plugin-url '
                  '--remote-control-session-name-prefix --setting-sources --settings --system-prompt '
                  '--system-prompt-file --append-system-prompt-file --system-prompt-snapshot'.split()),
}


def passthrough(app, args):
    switches = {'-h', '--help', '--version', '-V' if app == 'codex' else '-v'}
    if app == 'claude':
        switches |= {'-p', '--print', '--bg', '--background', '--cloud', '--bare', '--safe-mode'}
    first_positional = True
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            break
        if arg.split('=', 1)[0] in switches:
            return True
        if app == 'claude' and arg.startswith('-p') and not arg.startswith('--'):
            return True
        if arg in VALUE_FLAGS[app]:
            i += 2
            continue
        if app == 'claude' and arg.split('=', 1)[0] in {
            '-r', '--resume', '--from-pr', '-d', '--debug', '-w', '--worktree',
            '--remote-control', '--prompt-suggestions', '--add-dir', '--allowedTools',
            '--allowed-tools', '--disallowedTools', '--disallowed-tools', '--betas',
            '--file', '--mcp-config', '--tools',
        }:
            # A following word can be an optional/variadic value, not a subcommand.
            first_positional = False
        if not arg.startswith('-'):
            if first_positional and arg in SUBCOMMANDS[app]:
                return True
            first_positional = False
        i += 1
    return False


def tmux(*args):
    return subprocess.check_output([TMUX, *args], text=True).strip()


def configure(app, token):
    pane = os.environ['TMUX_PANE']
    session = tmux('display-message', '-p', '-t', pane, '#{session_id}')
    tmux('set-option', '-p', '-t', pane, '@agent-footer-app', app)
    tmux('set-option', '-p', '-t', pane, '@agent-footer-token', token)
    command = (f'{shlex.quote(PYTHON)} {shlex.quote(str(ROOT / "footer.py"))}'
               ' --socket #{q:socket_path} --pane #{pane_id} --width #{client_width}')
    options = {'status': 'on', 'status-position': 'bottom', 'status-interval': '2',
               'status-style': 'bg=#1a1b26,fg=#c0caf5',
               'status-format[0]': '#(' + command + ')'}
    for key, value in options.items():
        tmux('set-option', '-t', session, key, value)


def merge(base, extra):
    for key, value in extra.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            merge(base[key], value)
        else:
            base[key] = value
    return base


def claude_args(args, token):
    # Claude's --settings is a scalar option. Combine explicit settings into one
    # override so adding the bridge does not discard the caller's other settings.
    kept, extra = [], {}
    sources = {'user', 'project', 'local'}
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == '--':
            kept.extend(args[i:])
            break
        if arg == '--setting-sources' and i + 1 < len(args):
            sources = set(args[i + 1].split(','))
        elif arg.startswith('--setting-sources='):
            sources = set(arg.split('=', 1)[1].split(','))
        if arg == '--settings' or arg.startswith('--settings='):
            value = arg.split('=', 1)[1] if '=' in arg else args[i + 1]
            data = json.loads(value) if value.lstrip().startswith('{') else json.loads(Path(value).expanduser().read_text())
            merge(extra, data)
            i += 1 if '=' in arg else 2
            continue
        kept.append(arg)
        i += 1
    user_dir = Path(os.environ.get('CLAUDE_CONFIG_DIR', str(Path.home() / '.claude')))
    project = subprocess.run(['git', 'rev-parse', '--show-toplevel'], capture_output=True, text=True).stdout.strip()
    project_dir = Path(project or os.getcwd()) / '.claude'
    effective = {}
    for source, path in [('user', user_dir / 'settings.json'),
                         ('project', project_dir / 'settings.json'),
                         ('local', project_dir / 'settings.local.json')]:
        if source in sources:
            merge(effective, read_json(path))
    merge(effective, extra)
    original = effective.get('statusLine') or {}
    command = [PYTHON, str(ROOT / 'claude_status.py'), '--token', token]
    if original.get('command'):
        command += ['--forward', original['command']]
    extra['statusLine'] = {**original, 'type': 'command', 'command': shlex.join(command)}
    return ['--settings', json.dumps(extra), *kept]


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print('Usage: agent-tmux {codex|claude} [CLI arguments]')
        return
    app, *args = sys.argv[1:]
    if app not in SUBCOMMANDS:
        raise SystemExit('Usage: launch.py {codex|claude} [CLI arguments]')
    executable = shutil.which(app)
    if not executable:
        raise SystemExit(f'{app} is not on PATH')
    inside = args[:1] == ['--inside']
    if inside:
        args = args[1:]
    if passthrough(app, args) or (not inside and not (sys.stdin.isatty() and sys.stdout.isatty())):
        os.execv(executable, [executable, *args])
    if not shutil.which(TMUX):
        raise SystemExit('tmux is required for the interactive footer; install it and retry.')
    if inside or os.environ.get('TMUX'):
        token = uuid.uuid4().hex
        configured_args = (['--no-daemon', '-c', 'tui.status_line=[]', *args]
                           if app == 'codex' else claude_args(args, token))
        configure(app, token)
        os.execv(executable, [executable, *configured_args])
    session = app + '-' + uuid.uuid4().hex[:8]
    command = shlex.join([PYTHON, str(ROOT / 'launch.py'), app, '--inside', *args])
    environment = []
    for key in ['ITERM_SESSION_ID', 'ITERM_PROFILE', 'TERM_PROGRAM', 'TERM_PROGRAM_VERSION',
                'COLORTERM', 'CODEX_HOME', 'CLAUDE_CONFIG_DIR', 'XDG_CACHE_HOME', 'PATH']:
        if key in os.environ:
            environment += ['-e', f'{key}={os.environ[key]}']
    os.execv(TMUX, [TMUX, '-L', 'agent-footer', '-f', str(ROOT / 'tmux.conf'),
                   'new-session', '-s', session, '-c', os.getcwd(), *environment, command])


if __name__ == '__main__':
    main()
