#!/usr/bin/env python3
"""Cache Claude's statusLine metrics for tmux and forward its original status.

Claude runs this as its statusLine command, passing session JSON on stdin.
"""

import argparse
import json
import subprocess
import sys

from footer import (
    claude_cache_path,
    claude_state,
    is_valid_token,
    load_settings,
    save_json,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--token', required=True, help='Per-launch cache key')
    parser.add_argument(
        '--forward', default='', help="The user's original statusLine command"
    )
    args = parser.parse_args()
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


if __name__ == '__main__':
    main()
