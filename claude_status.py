#!/usr/bin/env python3
"""Receive Claude statusLine JSON, cache normalized metrics, preserve its current UI."""
import argparse
import json
import re
import subprocess
import sys

from footer import cache_dir, claude_state, save_json, settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--token', required=True)
    parser.add_argument('--forward', default='')
    args = parser.parse_args()
    raw = sys.stdin.read()
    try:
        if re.fullmatch(r'[a-f0-9]{32}', args.token):
            save_json(cache_dir() / f'claude-{args.token}.json', claude_state(json.loads(raw)))
    except (OSError, ValueError, TypeError, AttributeError):
        pass  # A cache failure must not break the existing status line.
    if args.forward and settings().get('claude_show_builtin_status', True):
        subprocess.run(args.forward, shell=True, input=raw, text=True, check=False)


if __name__ == '__main__':
    main()
