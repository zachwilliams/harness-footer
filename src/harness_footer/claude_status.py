"""The `claude-status` command: Claude Code's statusLine bridge.

Claude runs this with session JSON on stdin. It caches the usage for the tmux
footer, then runs the user's original statusLine command, if any.
"""

import argparse
import json
import subprocess
import sys

from harness_footer.claude_usage import claude_state
from harness_footer.common import (
    claude_cache_path,
    is_valid_token,
    load_settings,
    save_json,
)


def main(argv):
    parser = argparse.ArgumentParser(
        prog='harness-footer claude-status', description=__doc__
    )
    parser.add_argument('--token', required=True, help='Per-launch cache key')
    parser.add_argument(
        '--forward', default='', help="The user's original statusLine command"
    )
    args = parser.parse_args(argv)
    payload = sys.stdin.read()
    if is_valid_token(args.token):
        try:
            state = claude_state(json.loads(payload))
            save_json(claude_cache_path(args.token), state)
        except (OSError, ValueError, TypeError, AttributeError):
            pass  # A cache failure must not break the existing status line.
    if args.forward and load_settings()['claude_show_builtin_status']:
        subprocess.run(
            args.forward, shell=True, input=payload, text=True, check=False
        )
