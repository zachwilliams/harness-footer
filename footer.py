#!/usr/bin/env python3
"""Render the shared Codex/Claude tmux status line from local usage data."""

import argparse
import os
import re

from claude_usage import cached_claude_state, claude_state
from codex_usage import codex_state, read_rollout
from common import (
    LEGACY_PANE_APP_OPTION,
    LEGACY_PANE_TOKEN_OPTION,
    MINUTES_PER_DAY,
    PANE_APP_OPTION,
    PANE_TOKEN_OPTION,
    cache_dir,
    command_output,
    is_valid_token,
    load_settings,
    read_json,
)
from render import render, strip_styles, style


def git_output(cwd, *args):
    return command_output(['git', '--no-optional-locks', '-C', cwd, *args])


def git_segment(cwd):
    branch = git_output(
        cwd, 'symbolic-ref', '--quiet', '--short', 'HEAD'
    ) or git_output(cwd, 'rev-parse', '--short', 'HEAD')
    if not branch:
        return ''
    changes = git_output(
        cwd, 'status', '--porcelain', '--untracked-files=normal'
    )
    return f'[{branch}]*' if changes else f'[{branch}]'


def tmux_pane_option(name, legacy_name):
    return f'#{{?{name},#{{{name}}},#{{{legacy_name}}}}}'


def pane_state(socket, pane):
    """Return the usage state and working directory for a tmux pane."""
    if not socket or not re.fullmatch(r'%\d+', pane or ''):
        raise ValueError('Pass --socket and --pane, or --rollout')
    pane_format = '\t'.join(
        [
            '#{pane_pid}',
            '#{pane_current_path}',
            tmux_pane_option(PANE_APP_OPTION, LEGACY_PANE_APP_OPTION),
            tmux_pane_option(PANE_TOKEN_OPTION, LEGACY_PANE_TOKEN_OPTION),
        ]
    )
    query = ['display-message', '-p', '-t', pane, pane_format]
    fields = command_output(['tmux', '-S', socket, *query])
    pid, cwd, app, token = fields.split('\t')
    if app == 'claude' and is_valid_token(token):
        return cached_claude_state(token), cwd
    return codex_state(int(pid)), cwd


def demo_state(app):
    state = {
        'app': app,
        'model': 'GPT-6-Astra' if app == 'codex' else 'Opus',
        'window': 1_000_000,
        'rate_limits': {
            'primary': {
                'used_percent': 57,
                'window_minutes': 7 * MINUTES_PER_DAY,
            }
        },
    }
    if app == 'codex':
        state['total'] = 42_000
    return state


def parse_args():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--socket', help='tmux server socket path')
    parser.add_argument('--pane', help='tmux pane ID, such as %%1')
    parser.add_argument('--width', type=int, default=200)
    parser.add_argument(
        '--rollout', help='Explicit local transcript for preview/debugging'
    )
    parser.add_argument(
        '--plain', action='store_true', help='Omit tmux styles'
    )
    parser.add_argument(
        '--demo', type=int, help='Preview a context count in a 1M window'
    )
    parser.add_argument('--app', choices=['codex', 'claude'], default='codex')
    parser.add_argument(
        '--claude-json',
        help='Explicit statusLine payload for preview/debugging',
    )
    return parser.parse_args()


def main():
    args = parse_args()
    config = load_settings()
    if args.demo is not None:
        state = demo_state(args.app) | {'context': args.demo}
        cwd, git = '/example/my-project', '[main]*'
    else:
        if args.claude_json:
            state, cwd = claude_state(read_json(args.claude_json)), os.getcwd()
        elif args.rollout:
            state, cwd = read_rollout(args.rollout, cache_dir()), os.getcwd()
        else:
            state, cwd = pane_state(args.socket, args.pane)
        cwd = state.get('cwd') or cwd
        git = git_segment(cwd)
    line = render(state, cwd, git, config, args.width)
    print(strip_styles(line) if args.plain else line)


if __name__ == '__main__':
    try:
        main()
    except Exception:
        # A missing or incompatible rollout must never break the terminal.
        print(style(' harness-footer: usage unavailable', 'dim'))
