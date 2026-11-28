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
| accounts.py | Core | Named accounts: one login of a CLI in a home of its own. `AccountStore(root)` keeps `<root>/<backend>/<name>` (the directory tree is the whole registry, a link in an account's place is never followed) with `create` / `ensure` / `get` / `list` / `delete`, and `api_key_home(backend)`: a stable per-agent home under `<root>/_api_key/` for runs that bill an API key, never logged in to and never listed. A name is a lower-case slug (`[a-z0-9][a-z0-9_-]{0,31}`) because it is a path component and a URL segment. `Account.env()` is the environment that reaches the account, for probes, login and import. | ✅ |
| credential_store.py | Core | Detect / clear subscription credentials. It never writes one: a login is made by the CLI itself, so the host holds none it could mishandle, and a deployment that must not keep subscription logins can still see and remove ones left earlier. | ✅ |
| launch_env.py | Core | `cli_base_env`: the environment every launch of a CLI starts from (core variables, the variables that relocate the CLI's own login home, the managed Node on PATH for a CLI the isolated toolchain installed); shared by turns and probes so a probe sees what a turn will see. `account_env`: the variables that put a known CLI in the home of the account it runs as, applied after everything else a launch is given. | ✅ |
| login_session.py | Core | Drive the login of a `BROWSER_OAUTH` CLI (Codex) and stream structured AuthEvents: links and codes become PROMPT events (colour codes stripped, the CLI's own callback address never offered as a link), SUCCESS only when the CLI exits cleanly and reports a login, ERROR carries the CLI's last words. | ✅ |
| logout.py | Core | `log_out`: ask the CLI to log itself out (`logout_args`), then remove the credential files, and return the state the CLI reports afterwards. | ✅ |
| terminal_login.py | Core | `terminal_login`: the login of a CLI as a person types it in a terminal. `TerminalLogin` holds the CLI as the terminal will find it, its login arguments, the variables that keep the login in an account's home and a directory the CLI needs on `PATH`, and spells them for POSIX shells, PowerShell and cmd.exe (`render`, `commands`: a shell that cannot spell a path is left out). Starts nothing. | ✅ |
| login_state.py | Core | `probe_login` / `resolve_login_state`: the login state a CLI reports about itself (`claude auth status`, `codex login status`), the credential file only as the fallback. | ✅ |

## Design notes
- **Environment-aware paths**: `DriverSpec.resolve_home` honours per-CLI home
  overrides (`CODEX_HOME`, `CLAUDE_CONFIG_DIR`), so the control plane redirects a
  CLI's home to a persistent volume without any change here.
- **An account is a directory the CLI is relocated into**: two logins of the same CLI (a work and a personal
  subscription) cannot share the directory it keeps its login in. The host makes one directory per account under a root it
  chooses and hands it to the runtime as `RuntimeConfig.account_home`; the CLI then finds only that account's login,
  settings and history, and the login in the user's own home is left alone. How a CLI is relocated is part of its contract
  (`DriverSpec.isolated_env`): Claude `CLAUDE_CONFIG_DIR`, Codex `CODEX_HOME`, Qwen `QWEN_HOME`, Gemini `GEMINI_CLI_HOME`
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
  Measured against the real binaries (`tests/toolkits/acp/test_real_cli_login_flow.py`): each command a contract runs
  is still one the CLI accepts (the login the user types in a terminal included), and each CLI's own logout leaves a
  home with no login.
- **A login the host would have to relay is the user's to run**: `claude auth login --claudeai` prints a link and asks
  for the code its page shows, and Gemini and Qwen have no login command at all (their interface asks on the first start).
  Driving them from the host would put the user's code and the login it opens through the host, so they are
  `LoginStrategy.TERMINAL` and the host only describes the command (`terminal_login`): the CLI as the terminal will find
  it (its name when a terminal finds this very file under it, its path otherwise, with the managed Node ahead on `PATH`
  for a CLI the isolated toolchain installed), the variables that keep the login in an account's home
  (`DriverSpec.isolated_env`), spelled for POSIX shells, PowerShell and cmd.exe. The host then asks the CLI who is
  logged in (`resolve_login_state`). The host holds no credential and accepts none to store.
