# harness-footer

A tmux status bar for Codex CLI and Claude Code. It shows the model, context
usage, quota usage and cost of the session you're working in.

```text
claude | Opus 5.5 (1m) | project | [main]* | ctx[███░│░░░░░░░░░░░░░░░░] 173K 17% | quota[██████░░░░] 5h 42% 7d 57% | API est $1.23
```

| Segment | Example | What it shows |
| --- | --- | --- |
| Harness | `claude` | The CLI running in this pane: `claude` or `codex` |
| Model | `Opus 5.5 (1m)` | Model name and context window size |
| Working directory | `project` | Name of the current directory |
| Git branch | `[main]*` | Current branch; `*` means uncommitted changes |
| Context usage | `ctx[███░│░░…] 173K 17%` | Tokens in context and percentage of the window used |
| Quota usage | `quota[██████░░░░] 5h 42% 7d 57%` | Percentage used of each rate-limit window |
| Cost estimate | `API est $1.23` | Claude's estimate of the session cost at API prices |

The context bar is 20 blocks wide and covers the model's whole context window.
The `│` marks the context threshold, 200K tokens by default. The bar turns
yellow past the threshold and red at 90% of the window (the last two blocks).
I picked 200K because that's where I start to see performance decay. To change
it, set this in `settings.json`:

```json
"context_threshold": 200000
```

> [!WARNING]
> If you manage your windows with tmux, harness-footer isn't a good fit yet.
> Inside an existing tmux session it replaces that session's status bar until
> the session ends, and it doesn't set up Shift+Enter. Outside tmux it starts
> its own tmux server with no prefix key, so your tmux key bindings don't work
> there. See [Terminal behavior](#terminal-behavior).

## Install

You need macOS or Linux (including WSL), Python 3.11+, tmux 3.2+, Git and
`lsof`, plus Codex CLI or Claude Code.

```sh
uv tool install git+https://github.com/zachwilliams/harness-footer
harness-footer setup
```

Open a new shell and run `claude` or `codex` as usual. `pipx install` works
too if you don't use uv.

`harness-footer setup` adds these lines to `~/.zshrc` or `~/.bashrc`, backing
up the file first:

```sh
# >>> harness-footer >>>
claude() { harness-footer claude "$@"; }
codex() { harness-footer codex "$@"; }
# <<< harness-footer <<<
```

Use `--shell bash` or `--shell zsh` to pick the shell, `--shell-rc PATH` to
edit a different startup file, or `--print` to print the lines and add them
yourself. To skip the footer for one run, use `command claude` or
`command codex`.

## Settings

`harness-footer setup` creates `~/.config/harness-footer/settings.json` (or
`$XDG_CONFIG_HOME/harness-footer/settings.json`).

| Setting | Default | Effect |
| --- | --- | --- |
| `context_threshold` | `200000` | Tokens at which the context bar turns yellow |
| `claude_show_builtin_status` | `true` | Set to `false` to hide Claude's own status line |

## Uninstall

1. Open the startup file `setup` edited (`~/.zshrc` or `~/.bashrc`).
2. Delete everything from `# >>> harness-footer >>>` to
   `# <<< harness-footer <<<`, including those two lines.
3. Run `uv tool uninstall harness-footer`.
4. Optionally delete `~/.config/harness-footer` and `~/.cache/harness-footer`.

harness-footer never changes your Claude or Codex settings, so there is
nothing else to undo.

## Details

### How it works

`harness-footer claude` starts Claude in a tmux session on a dedicated
`harness-footer` tmux server. Each terminal tab or split gets its own session.
The status bar runs `harness-footer status` every two seconds.

- **Claude:** each launch passes Claude a `--settings` override whose
  `statusLine` command is `harness-footer claude-status`. That command caches
  the usage data Claude sends it, then runs your own status line command if
  you have one. Your other settings are merged in and kept.
- **Codex:** the footer finds the `codex` process in the pane and reads its
  rollout file from where it last stopped. Codex runs with `--no-daemon` so
  the file belongs to that process.

Cached usage is written to `~/.cache/harness-footer` (or
`$XDG_CACHE_HOME/harness-footer`) with mode 0600. It never contains
transcript text or credentials, and the footer makes no network requests.

### What the numbers mean

- **Context:** for Claude, input tokens plus cache reads and writes, matching
  Claude's own context percentage. After startup or compaction the footer
  shows `ctx: awaiting usage` until new numbers arrive.
- **Quota:** percentage used, per window. Hidden when the CLI doesn't report
  it. Enterprise spend limits appear as `spend`.
- **Cost:** Claude's `cost.total_cost_usd` for the whole session, estimated at
  API prices. What you're billed can differ. Codex doesn't report cost, so no
  cost is shown for Codex.
- **Narrow terminals:** labels shorten, then drop, from the model inward.
  The harness name and context usage always stay.

### Terminal behavior

The dedicated tmux server is configured so the CLIs behave as they do
outside tmux:

- Shift+Enter and other modified keys reach the CLI.
- There is no prefix key, so Ctrl+B and every other shortcut reach the CLI.
- The mouse wheel scrolls tmux history, since your terminal's scrollback can't
  see output inside tmux. Hold Option in iTerm2 for native text selection.
- Notifications, clipboard writes, focus changes and window titles reach the
  terminal.

Inside your own tmux session, harness-footer runs in the current pane. It sets
that session's `status`, `status-position`, `status-interval`, `status-style`
and `status-format[0]`, which stay until the session ends. It doesn't change
your server options, so copy the `extended-keys` lines from `tmux.conf` into
your own config if you want Shift+Enter. The footer follows the active pane.

Exiting the CLI closes its tmux session. To detach or reattach from another
terminal:

```sh
tmux -L harness-footer list-sessions
tmux -L harness-footer detach-client -s claude-SESSION_ID
tmux -L harness-footer attach-session -t claude-SESSION_ID
```

After editing `src/harness_footer/tmux.conf`, apply it to a running server
with `tmux -L harness-footer source-file src/harness_footer/tmux.conf`.

The footer is skipped for help, version, admin subcommands, `codex exec`,
`claude -p`, redirected input or output, and Claude's background, cloud, bare
and safe modes.

### Limitations

- Tested on macOS with tmux 3.7c, Codex 0.156.1 and Claude Code 2.1.280.
  Linux should work but hasn't been tested in a live session.
- Managed Claude policies that block custom status line commands stop the
  Claude metrics.
- Codex usage from a remote app-server isn't supported. Changes to the Codex
  rollout format may need reader updates.

### Project layout

The code lives in `src/harness_footer/`:

| File | Role |
| --- | --- |
| `cli.py` | The `harness-footer` command and its subcommands |
| `launch.py` | `claude` and `codex`: run the CLI inside tmux with the footer |
| `footer.py` | `status`: the tmux status command |
| `render.py` | Formats usage as a status line that fits the width |
| `claude_usage.py` | Reads Claude's status line data |
| `codex_usage.py` | Finds and reads Codex rollout files |
| `claude_status.py` | `claude-status`: Claude's status line command |
| `shell_setup.py` | `setup`: adds the shell functions |
| `common.py` | Settings, file locations and shared helpers |
| `tmux.conf` | Options for the dedicated tmux server |

### Development

```sh
uv tool install --editable .          # use your checkout; edits apply live
uv run python -m unittest -v          # tests, in tests/
uv run ruff format --check . && uv run ruff check .   # PEP 8 style
harness-footer status --demo 173000 --plain           # preview the footer
```

The tmux tests run on an isolated tmux server with fake CLIs, so they send no
model requests. They're skipped when tmux isn't installed.

## License

[MIT](LICENSE)
