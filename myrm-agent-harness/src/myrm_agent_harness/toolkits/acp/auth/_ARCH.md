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
| _profiles.py | Internal | Per-backend CLI knowledge: credential paths, login command, strategy, api-key env, baseline launch args, and the permission-mode → argv mapping. Sole owner of CLI invocation arguments. | ✅ |
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
- **Single owner of CLI arguments**: `resolve_cli_args` merges the baseline launch
  args with the configured args and the permission-mode mapping. The mapping is the
  sole writer of permission flags, so an explicit flag in a stored config (a sandbox
  mode, a `--approval-mode=plan` from initial setup, or a `--yolo` alias) is stripped
  rather than duplicated — both the spaced and the `--flag=value` spellings.
  The merge works on options (a flag with the values after it), not on single
  tokens: a config restating a baseline option (a preset's `exec --json`) adds
  nothing, while the config's own repeated options all stay (`-c a=1 -c b=2` is two
  settings; dropping the second `-c` as a duplicate token would lose one of them).
  This matters because these parsers are last-wins (clap `ArgAction::Set` for codex,
  non-array yargs for gemini/qwen), so a duplicate would silently override the mode
  the user selected instead of failing loudly. Stripping is scoped per backend: `-s` is
  codex's sandbox *mode* but gemini's boolean sandbox *toggle*, so a shared strip list
  would silently disable sandboxing a gemini user explicitly asked for. Unknown CLIs are
  returned untouched, since no contract exists for them and their flags may mean
  something unrelated.
- **The ACP channel has no mode mapping**: an ACP agent asks the host for permission, and the host's policy answers
  (`AcpPermissionPolicy`). An agent started with `--yolo` or `--approval-mode` never asks, so the policy's
  `safe` would be bypassed without a trace. `without_approval_flags` therefore drops those flags (the spaced and the
  `--flag=value` spellings, with their values) from the configured args of a known agent at launch. Codex's sandbox
  flags stay, since they limit what an approved action may do rather than whether anyone is asked, and `plan` is
  never added: it would turn an agent that asks into one that refuses. An unknown command is returned untouched.
- **Baseline args never carry permission flags**: `cli_launch_args` holds only the
  non-interactive output/transport arguments. Keeping permission flags out of it is
  what makes the mode mapping genuinely single-owner; otherwise a baseline flag fights
  the mapping and the last-wins parser resolves that fight invisibly.
