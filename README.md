# agent-tmux

A shared, pinned tmux status bar for Codex CLI and Claude Code. Plain Python,
no Python packages, no model requests, and no persistent changes to agent settings.

Interactive `codex` and `claude` use the same bottom status bar:

```text
codex | gpt-6-astra (1m) | project | [main]* | ctx[█████████░│░░░░░] 173K 17% | quota[██████░░░░] 7d 57%
claude | Opus 5.5 (1m) | project | [main]* | ctx[█████████░│░░░░░] 173K 17% | quota[██████░░░░] 5h 42% 7d 57% | API est $1.23
```

These are illustrative values. Actual values come from each active session.
Colors, thresholds, gauges, Git status, directory names, and responsive layout
are shared. The app prefix is bold and stays visible as the terminal narrows.
Model windows use lowercase `k`/`m` without the word `context`. Cumulative
tokens are not displayed.

## Requirements

- macOS or Linux, including WSL; native Windows is not supported.
- Python 3.10+, tmux 3.2+, Git, and `lsof`, available on `PATH`.
- Codex CLI and/or Claude Code installed and authenticated separately.
- Bash or Zsh for automatic shell integration; a UTF-8 terminal.

Validated locally with macOS, tmux 3.7c, Codex 0.156.1, and Claude Code 2.1.280.
Linux uses the same tools but has not yet been validated in a live session.
The terminal demos and tests do not require either agent to be authenticated.

## Install

Clone or unpack the project wherever you want to keep it, for example
`~/.config/agent-tmux`, then run:

```sh
cd ~/.config/agent-tmux
python3 install.py
. ./activate.sh
claude  # or codex
```

The installer checks dependencies, creates `activate.sh` and `bin/agent-tmux`,
and adds a marked source block to your shell startup file. Zsh uses
`${ZDOTDIR:-$HOME}/.zshrc`; Bash uses `~/.bashrc`. It backs up an existing startup
file before editing and preserves your `settings.json`. Re-running updates the
same block without duplicating it. New interactive shells load the wrappers.
On macOS, a Bash login shell must source `~/.bashrc`, or select your login startup
file explicitly with `--shell-rc ~/.bash_profile`.

The checkout is the installation by default, so source changes apply directly.
No hardcoded username, Homebrew prefix, or Python version is required. Generated
files record the interpreter used to run the installer; re-run it after moving
the checkout or removing that interpreter.

```sh
# Choose a shell or a custom startup file:
python3 install.py --shell zsh --shell-rc ~/.config/zsh/.zshrc.local

# Generate wrappers, with no automatic shell startup edit:
python3 install.py --no-shell

# Optional: install a separate runtime copy under ~/.local/share/agent-tmux:
python3 install.py --prefix ~/.local

# Direct launch without sourcing shell functions:
./bin/agent-tmux claude
./bin/agent-tmux codex resume
```

Use the activation path printed by the installer when installing a separate copy.
With `--prefix`, the standalone command is in `PREFIX/bin/agent-tmux`.
Already-running sessions continue running; edits to the renderer take effect at
the next refresh, while launcher changes require a new session.

## Configuration

The installer creates `settings.json` beside `footer.py` from
`settings.example.json` if it is missing. `settings.json` is ignored by Git;
your thresholds and preferences are not included when you share the repository.
The defaults also work directly from a fresh checkout before installation.

## Compare, then hide Claude's internal status

Claude's existing status command is forwarded its original JSON and remains
visible by default. No persistent Claude settings are changed. After comparing,
set this in your installation's `settings.json`:

```json
"claude_show_builtin_status": false
```

The bridge then emits no internal status text while continuing to feed tmux.
This takes effect on Claude's next status update. Setting it back to `true`
restores the original command's output. Removing `statusLine` from your user
Claude settings also works for future launches, but is unnecessary.

## Meaning and data sources

- The context gauge's first ten cells cover 0–200K tokens; its five-cell tail
  covers the rest of a larger context window. Yellow starts at 150K, red at 200K.
  Smaller windows also warn at 50%/80% usage. Edit `settings.json` to adjust.
- `[branch]*` includes staged, unstaged, and untracked changes.
- Quota percentages are used, not remaining. Quota is hidden when unavailable.
- Codex uses the existing incremental rollout reader and `--no-daemon` to bind
  usage to the pane's process.
- Claude uses its documented statusLine JSON callback. Context includes input,
  cache reads, and cache writes, excluding output, matching Claude's context
  percentage. Startup/compaction waits for new usage.
- Claude's `API est $…` is the reported `cost.total_cost_usd` for the whole session.
  It is a client-side API price estimate, not an invoice or an overage-only amount.
  It is displayed whenever reported, without guessing billing mode from quota
  presence or percentage. Enterprise gateway spend limits are shown when supplied.
- Codex's inspected local usage records expose tokens and quota/credit metadata,
  not a session dollar cost. The footer hides cost when unavailable.
  Credit balances and quota percentages are not converted to dollars.
  Claude cost data is also hidden when missing; reported zero stays `$0.00`.
  Neither feed establishes actual enterprise contract charges or overage owed.
- Narrow layouts shorten or omit model, directory, and Git labels before dropping
  cost. Extremely narrow layouts retain only the app and compact context display.
- Each Claude launch gets a unique cache key, even with simultaneous sessions in
  the same directory. Normalized metrics are written atomically with mode 0600
  under `~/.cache/agent-tmux` (or `$XDG_CACHE_HOME/agent-tmux`). No transcript
  content or credentials are cached and the footer makes no API requests.
- The renderer refreshes every two seconds; metrics change when each CLI reports
  usage. Git and directory information are rendered independently.

See [Claude's status-line data documentation](https://code.claude.com/docs/en/statusline).

## Terminal behavior

Each ordinary terminal pane gets its own tmux session on the `agent-footer`
server. Continue using iTerm tabs and splits normally. In an existing tmux
session, the status bar follows the active pane instead of nesting tmux.
The status configuration is applied to that existing session.

Exiting the CLI returns to the original shell. Ctrl+B then D detaches; reattach:

```sh
tmux -L agent-footer list-sessions
tmux -L agent-footer attach-session -t claude-SESSION_ID
```

Help, version, administrative subcommands, `codex exec`, `claude -p`, redirected
input/output, and Claude background/cloud/bare/safe modes bypass the wrapper.
`command claude` and `command codex` bypass it explicitly.

Claude's bridge is passed through a per-launch `--settings` override. Explicit
settings are merged so other supplied settings are retained. It forwards the
status command from user/project/local settings or explicit `--settings`.
Managed policies that disable custom status commands can prevent Claude metrics
from appearing. Remote Codex app-server usage is not supported by the local
rollout reader. Codex rollout schema changes may require reader updates.

## Verification

```sh
cd ~/.config/agent-tmux
python3 -m unittest discover -v
python3 test_tmux.py
python3 footer.py --app codex --demo 173000 --plain
python3 footer.py --app claude --demo 173000 --plain
```

The tmux test uses an isolated temporary socket and fake CLI processes; it checks
independent Claude metrics, Codex launch flags, bottom positioning, and 140/80
column layouts without sending model requests. A live conversation remains the
final visual check in your terminal.

Installer tests use temporary directories, including paths with spaces and
apostrophes. They check argument forwarding, settings preservation, startup-file
backups, and repeated installation. Generated activation scripts, local settings,
bytecode, and distribution archives are excluded from Git.

## Uninstall

Remove the `# >>> agent-tmux >>>` through `# <<< agent-tmux <<<` block from the
startup file reported by the installer, then open a new shell. To stop using the
wrappers immediately, run `unset -f codex claude`. Your agent configurations are
unchanged. You can then remove the checkout/runtime installation, its generated
wrapper, and `~/.cache/agent-tmux` if you no longer need them.

## License

[MIT](LICENSE).
