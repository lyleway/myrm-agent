# auth/

## Overview
Subscription authentication subsystem for external CLI agent backends — lets a
delegated CLI (Codex / Claude Code / Gemini / Qwen …) run on the user's own model
subscription instead of a metered API key. Framework-level mechanisms only; the
business layer drives login, status badges and logout, and decides
whether the deployment may keep a user's subscription login at all.

## File & Submodule Index

| File | Role | Description | I/O/P |
|------|------|-------------|-------|
| __init__.py | Package | Public surface of the ACP auth subsystem. | ✅ |
| _profiles.py | Internal | The contract of each known agent: credential paths, the commands that log in (`login_command(remote=…)`: the flow for a browser on this machine or on another), log out and report the login, who runs the login (`LoginStrategy`: the host for a browser or device-code flow, the user in a terminal), api-key env, and the transport it is driven over (`cli` with its baseline launch args, `acp` with its entry args and the session mode that asks the host, or `app_server` with the subcommand that starts its own server). Names how the CLI is relocated into a home of its own (`isolated_env`, `relocation_vars`). Resolves a name (`profile_for`) or an exact executable (`command_profile`, `effective_transport`) to its contract. | ✅ |
| accounts.py | Core | Named accounts: one login of a CLI in a home of its own. `AccountStore(root)` keeps `<root>/<backend>/<name>` (the directory tree is the whole registry, a link in an account's place is never followed) with `create` / `ensure` / `get` / `list` / `delete`, and `api_key_home(backend)`: a stable per-agent home under `<root>/_api_key/` for runs that bill an API key, never logged in to and never listed. A name is a lower-case slug (`[a-z0-9][a-z0-9_-]{0,31}`) because it is a path component and a URL segment. `Account.env()` is the environment that reaches the account, for probes, login and import. | ✅ |
| _launch_args.py | Internal | The command line of a known agent: `resolve_cli_args` (permission mode → flags of a CLI agent), `resolve_acp_args` (the entry args of an ACP agent, every approval-skipping flag dropped and reported) and `resolve_app_server_args` (the subcommand of an app-server agent plus the configuration overrides it understands, everything else dropped and reported). Sole owner of the permission flags of every known agent. | ✅ |
| credential_store.py | Core | Detect / clear subscription credentials. It never writes one: a login is made by the CLI itself, so the host holds none it could mishandle, and a deployment that must not keep subscription logins can still see and remove ones left earlier. | ✅ |
| launch_env.py | Core | `cli_base_env`: the environment every launch of a CLI starts from (core variables, the variables that relocate the CLI's own login home, the managed Node on PATH for a CLI the isolated toolchain installed); shared by turns and probes so a probe sees what a turn will see. `account_env`: the variables that put a known CLI in the home of the account it runs as, applied after everything else a launch is given. | ✅ |
| login_session.py | Core | Drive the login of a `BROWSER_OAUTH` CLI (Codex) and stream structured AuthEvents: links and codes become PROMPT events (colour codes stripped, the CLI's own callback address never offered as a link), SUCCESS only when the CLI exits cleanly and reports a login, ERROR carries the CLI's last words. | ✅ |
| logout.py | Core | `log_out`: ask the CLI to log itself out (`logout_args`), then remove the credential files, and return the state the CLI reports afterwards. | ✅ |
| terminal_login.py | Core | `terminal_login`: the login of a CLI as a person types it in a terminal. `TerminalLogin` holds the CLI as the terminal will find it, its login arguments, the variables that keep the login in an account's home and a directory the CLI needs on `PATH`, and spells them for POSIX shells, PowerShell and cmd.exe (`render`, `commands`: a shell that cannot spell a path is left out). Starts nothing. | ✅ |
| login_state.py | Core | `probe_login` / `resolve_login_state`: the login state a CLI reports about itself (`claude auth status`, `codex login status`), the credential file only as the fallback. | ✅ |

## Design notes
- **Environment-aware paths**: `AuthProfile.resolve_home` honours per-CLI home
  overrides (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`), so the control plane redirects a
  CLI's home to a persistent volume without any change here.
- **An account is a directory the CLI is relocated into**: two logins of the same CLI (a work and a personal
  subscription) cannot share the directory it keeps its login in. The host makes one directory per account under a root it
  chooses and hands it to the runtime as `RuntimeConfig.account_home`; the CLI then finds only that account's login,
  settings and history, and the login in the user's own home is left alone. How a CLI is relocated is part of its contract
  (`AuthProfile.isolated_env`): Claude `CLAUDE_CONFIG_DIR`, Codex `CODEX_HOME`, Qwen `QWEN_HOME`, Gemini `GEMINI_CLI_HOME`
  (its state is under `<root>/.gemini`) together with `GEMINI_FORCE_FILE_STORAGE=true`, since Gemini otherwise keeps its
  credential in the system keychain that every home shares and two accounts would silently share one login. The home is a typed
  field applied after the agent's configured environment, because the variables of Gemini and Qwen carry a provider prefix
  that a subscription-mode launch strips from that mapping as a secret; it must be an absolute path and the command a known
  agent, which `RuntimePool.register` checks when the agent is registered. No binding of a session to an account is stored:
  an agent moved to another account no longer finds the remembered session there, which the stale-resume retry turns into a
  new session with a notice. Measured against the real binaries (`tests/toolkits/acp/test_real_cli_account_home.py`): a turn
  as an account writes everything inside the account's home while the process's own home stays empty, a conversation
  continues in its own account only, and the login one account holds is the login the CLI reports for that account alone.
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
- **A login is done when the CLI says so**: success needs a clean exit and a CLI that reports a login
  (`resolve_login_state`; the credential file only for a CLI with no status command). The file cannot be the judge:
  Claude Code on macOS keeps its login in the Keychain and leaves no file, and a file left from before says nothing
  about the attempt that just ended. A failed login ends with the CLI's own last words (a refused connection, a
  workspace that forbids it). What the user sees is cleaned first: Codex colours
  its output even into a pipe and ignores `NO_COLOR`, which put escape sequences into the link and the code; its link
  and its code arrive on separate lines, so a consumer keeps the latest of each; a code is two groups of four or five
  characters and is looked for outside links, whose query holds upper-case runs of its own; and the address of the
  CLI's own callback server (`http://localhost:1455`) is not a page for the user to open.
- **Two browsers, two flows**: `codex login` serves its callback on the CLI's machine, which only a browser on that
  machine reaches; the CLI itself says to use `codex login --device-auth` on a remote or headless machine. `remote_login_args`
  names that flow and `CliLoginSession(remote=True)` runs it for a user whose browser is elsewhere. A flow that cannot
  start surfaces the CLI's own words.
- **Logout is the CLI's too**: deleting credential files cannot remove a login kept elsewhere, so `log_out` asks the
  CLI first (`claude auth logout`, `codex logout`), with the environment of its home and without the operator's provider
  keys, then removes the files (the whole login of gemini and qwen, which have no logout command).
  A CLI that fails to log out does not keep the files; the state it reports afterwards tells whether a login remains.
  Measured against the real binaries (`tests/toolkits/acp/test_real_cli_login_flow.py`): each command a profile runs
  is still one the CLI accepts (the login the user types in a terminal included), and each CLI's own logout leaves a
  home with no login.
- **A login the host would have to relay is the user's to run**: `claude auth login --claudeai` prints a link and asks
  for the code its page shows, and Gemini and Qwen have no login command at all (their interface asks on the first start).
  Driving them from the host would put the user's code and the login it opens through the host, so they are
  `LoginStrategy.TERMINAL` and the host only describes the command (`terminal_login`): the CLI as the terminal will find
  it (its name when a terminal finds this very file under it, its path otherwise, with the managed Node ahead on `PATH`
  for a CLI the isolated toolchain installed), the variables that keep the login in an account's home
  (`AuthProfile.isolated_env`), spelled for POSIX shells, PowerShell and cmd.exe. The host then asks the CLI who is
  logged in (`resolve_login_state`). The host holds no credential and accepts none to store.
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
