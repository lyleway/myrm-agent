# drivers/

## Overview
The contract of every known external agent (Codex / Claude Code / Gemini / Qwen): where its login lives, how it
logs in and out, which transport drives it, which arguments start it, which flags decide its permissions.
Login, status, launch and the settings screens read one `DriverSpec` instead of each keeping a copy of what a CLI
is, so a fact changes in one place. Framework-level and business-free: a host that embeds the harness gets the
same contract.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Public surface of the driver registry. | ✅ |
| launch.py | Core | The command line of a known agent: `resolve_cli_args` (permission mode → flags of a CLI agent), `resolve_acp_args` (the entry args of an ACP agent, every approval-skipping flag dropped and reported) and `resolve_app_server_args` (the subcommand of an app-server agent plus the configuration overrides it understands, everything else dropped and reported). Sole owner of the permission flags of every known agent. | ✅ |
| spec.py | Core | The contract of each known agent: credential paths, the commands that log in (`login_command(remote=…)`: the flow for a browser on this machine or on another), log out and report the login, who runs the login (`LoginStrategy`: the host for a browser or device-code flow, the user in a terminal), api-key env, and the transport it is driven over (`cli` with its baseline launch args, `acp` with its entry args and the session mode that asks the host, or `app_server` with the subcommand that starts its own server). Names how the CLI is relocated into a home of its own (`isolated_env`, `relocation_vars`). Resolves a name (`driver_for`) or an exact executable (`command_driver`, `effective_transport`) to its contract. | ✅ |

## Design notes
- **The contract names the transport**: `DriverSpec.transport` says how a known agent is driven. Claude Code is
  spawned per turn (`cli`); Gemini and Qwen speak ACP (`acp`); Codex runs its own server (`app_server`). The choice belongs to the agent, not to the type a
  user stored for it, so `effective_transport` returns the contract's transport for a command that exactly is the agent
  and leaves every other command's stored choice alone (an unrecognised value included, for the layer that stored it to
  report). The exact executable name decides (`command_driver`: the basename, lower-cased, without its extension):
  an adapter or wrapper that merely carries an agent's name (`claude-code-acp`, `gemini-wrapper`) is a command of its
  own with no contract, whereas `driver_for` also accepts such a name, which suits resolving a login by a configured
  name. `RuntimePool.register` applies the rule, so a config stored as `cli` for `gemini` runs over ACP, and one for
  `codex` over its app server, without a migration.
- **Single owner of CLI arguments**: `resolve_cli_args` merges the baseline launch
  args with the configured args and the permission-mode mapping. The mapping is the
  sole writer of permission flags, so an explicit flag in a stored config (a permission mode or a
  `--dangerously-skip-permissions`) is stripped rather than duplicated — both the spaced
  and the `--flag=value` spellings.
  The merge works on options (a flag with the values after it), not on single
  tokens: a config restating a baseline option (a preset's `--output-format stream-json`) adds
  nothing, while the config's own repeated options all stay (`--add-dir a --add-dir b` is two
  settings; dropping the second `--add-dir` as a duplicate token would lose one of them).
  This matters because a duplicated option is resolved by the CLI's own parser invisibly, so a
  stale flag would override the mode the user selected instead of failing loudly. Stripping is scoped per backend: a
  flag name can mean something else in another CLI (gemini's `--sandbox=true` is the user's own toggle), so a shared
  strip list would silently disable what a user explicitly asked for. Unknown CLIs are
  returned untouched, since no contract exists for them and their flags may mean
  something unrelated.
- **An app-server agent is launched by its contract and given its policy per turn**: Codex's approval policy and
  sandbox are parameters of each turn, so no flag can choose them and `resolve_app_server_args` has nothing to map. It
  returns the contract's subcommand (`app-server`) followed by what the config may still carry: `-c/--config`
  overrides, `--enable`/`--disable` feature switches and `--strict-config`, with `-m/--model` and `-p/--profile` of a
  config written for `codex exec` translated to the override they stand for. Everything else (the `exec` word, the
  sandbox and approval flags) is dropped and reported for a WARNING. In `api_key` mode the server also gets
  `-c cli_auth_credentials_store="ephemeral"`: the app server reads no key from its environment, so the runtime hands
  it the login in memory and the key is never written to the agent's home. A command that is not a known app-server
  agent keeps its args as configured.
- **An ACP agent is launched by its contract**: the agent asks the host for permission and the host's policy answers
  (`AcpPermissionPolicy`), so there is no mode mapping to write. `resolve_acp_args` returns the contract's entry args
  (`--acp --approval-mode default`) followed by the configured args, minus any option the contract already states and
  minus every flag that would make the agent act without asking (`--yolo`, `-y`, `--approval-mode`, spaced and
  `--flag=value`, with their values), which `AcpLaunch.dropped` reports for a WARNING. The explicit
  `--approval-mode default` is not redundant: measured on Gemini 0.71.1 and Qwen 0.28.6, an agent started with
  `--acp` alone takes its session mode from its own settings (`autoEdit` for Gemini, `yolo` for Qwen, whose own default
  is the model-judged `auto`), so it would act without ever asking; with the flag both report `default` whatever the
  settings say, in a folder they trust or not. `plan` is never used: it would turn an agent that asks into one that
  refuses. A flag an older preset stored (`--output-format stream-json`) does not stop either agent from starting, so
  stored configs need no migration. A command that is not a known ACP agent keeps its args as configured.
- **Baseline args never carry permission flags**: `cli_launch_args` holds only the
  non-interactive output/transport arguments. Keeping permission flags out of it is
  what makes the mode mapping genuinely single-owner; otherwise a baseline flag fights
  the mapping and the last-wins parser resolves that fight invisibly.
