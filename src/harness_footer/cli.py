"""The `harness-footer` command line."""

import sys

from harness_footer import claude_status, footer, launch, shell_setup

USAGE = """\
usage: harness-footer <command> [arguments]

commands:
  claude [args]   run Claude Code with the footer
  codex [args]    run Codex CLI with the footer
  status          print the footer line (try: status --demo 173000 --plain)
  setup           add the claude and codex shell functions to your shell
  claude-status   Claude's statusLine command, used internally
"""


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ('-h', '--help'):
        print(USAGE, end='')
        return
    command, args = argv[0], argv[1:]
    if command in launch.SUBCOMMANDS:
        launch.main(command, args)
    elif command == 'status':
        footer.main(args)
    elif command == 'claude-status':
        claude_status.main(args)
    elif command == 'setup':
        shell_setup.main(args)
    else:
        raise SystemExit(f'Unknown command: {command}\n\n{USAGE}')
