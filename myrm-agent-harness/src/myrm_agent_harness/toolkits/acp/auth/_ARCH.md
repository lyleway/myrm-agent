# auth/

## Overview
Subscription authentication subsystem for external CLI agent backends — lets a
delegated CLI (Codex / Claude Code / Gemini / Qwen …) run on the user's own model
subscription instead of a metered API key. Framework-level mechanisms only; the
business layer drives GUI login, status badges, and credential persistence, and decides
whether the deployment may keep a user's subscription credential at all.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Public surface of the ACP auth subsystem. | ✅ |
| _profiles.py | Internal | The contract of each known agent: credential paths, login command, strategy, api-key env, and the transport it is driven over (`cli` with its baseline launch args, `acp` with its entry args and the session mode that asks the host, or `app_server` with the subcommand that starts its own server). Resolves a name (`profile_for`) or an exact executable (`command_profile`, `effective_transport`) to its contract. | ✅ |
| _launch_args.py | Internal | The command line of a known agent: `resolve_cli_args` (permission mode → flags of a CLI agent), `resolve_acp_args` (the entry args of an ACP agent, every approval-skipping flag dropped and reported) and `resolve_app_server_args` (the subcommand of an app-server agent plus the configuration overrides it understands, everything else dropped and reported). Sole owner of the permission flags of every known agent. | ✅ |
| credential_store.py | Core | Detect / import / clear subscription credentials (atomic, owner-only writes). A store built with `accepts_new_credentials=False` refuses an import before writing anything and still detects and clears, so a deployment that must not hold subscription credentials can still remove ones stored earlier. | ✅ |
| launch_env.py | Core | `cli_base_env`: the environment every launch of a CLI starts from (core variables, the CLI's own login-home variable, the managed Node on PATH for a CLI the isolated toolchain installed); shared by turns and probes so a probe sees what a turn will see. | ✅ |
| login_session.py | Core | Drive `<cli> login` and stream structured AuthEvents (URL/code → SUCCESS/ERROR). | ✅ |
| login_state.py | Core | `probe_login` / `resolve_login_state`: the login state a CLI reports about itself (`claude auth status`, `codex login status`), the credential file only as the fallback. | ✅ |

## Design notes
- **Environment-aware paths**: `AuthProfile.resolve_home` honours per-CLI home
  overrides (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`), so the control plane redirects a
  CLI's home to a persistent volume without any change here.
- **Auth never enters the model**: credentials and auth state stay in the backend /
  config layers; they are never injected into prompts or tool descriptions, so the
  prompt prefix cache is unaffected.
- **The CLI knows where its login is**: a credential-file check cannot see a login kept
  elsewhere (Claude Code on macOS uses the Keychain) and trusts a file the CLI may not
  honour, so a CLI with a status command (`claude auth status`, `codex login status`) is asked
  and its answer wins; gemini and qwen have none and are judged by their file. Both CLIs were
  measured to answer without a network call, claude with JSON on stdout (exit 1 when logged
  out), codex on stderr (exit 0 / 1). The runtime still never blocks a turn on the answer: the
  CLI's own not-logged-in error surfaces as `AUTH_FAILED` carrying the CLI's own words, and
  visibility is delivered by status badges (`resolve_login_state`, `BackendDetector.detect_with_auth`).
- **Import is the universal fallback**: where scripted login is unavailable
  (`scriptable_login` is False) or inconvenient, the user pastes a credential blob
  captured elsewhere and `CredentialStore.import_credential` persists it safely.
- **The contract names the transport**: `AuthProfile.transport` says how a known agent is driven. Claude Code is
  spawned per turn (`cli`); Gemini and Qwen speak ACP (`acp`); Codex runs its own server (`app_server`). The choice belongs to the agent, not to the type a
  user stored for it, so `effective_transport` returns the contract's transport for a command that exactly is the agent
  and leaves every other command's stored choice alone (an unrecognised value included, for the layer that stored it to
  report). The exact executable name decides (`command_profile`: the basename, lower-cased, without its extension):
  an adapter or wrapper that merely carries an agent's name (`claude-code-acp`, `gemini-wrapper`) is a command of its
  own with no contract, whereas `profile_for` also accepts such a name, which suits resolving a login by a configured
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
