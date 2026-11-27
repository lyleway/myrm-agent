/**
 * [INPUT]
 * - @/services/config::ExternalAgentConfig (POS: one stored external agent entry)
 * - @/services/external-agents::ExternalAgentAuthStatus (POS: what the server reports for a known agent, its transport included)
 *
 * [OUTPUT]
 * - DEFAULT_AGENT, AGENT_PRESETS: the blank entry and the one-click presets of the add form
 * - PERMISSION_MODE_OPTIONS, permissionHintKey, normalizePermissionMode: the permission modes on offer and what each
 *   means for the agent being edited
 * - knownBackendOfCommand, backendKeyFromCommand: command → the known agent it is, command → the agent its login belongs to
 * - isValidAccountName, accountBackend, detachAccount: the account names the server accepts, the agent an account applies
 *   to, and the agents left after an account is removed
 * - contractTransport: the transport a known agent's contract fixes for a command
 * - ExternalAgentDraft, EnvRow, toDraft, newEnvRow, envRowProblems, effectiveAuthMode, cleanAgent: the editable
 *   form of an agent and the rules for turning it back into what is stored
 *
 * [POS]
 * Pure data and rules of the external agent editor; no React, so every rule is unit-testable.
 */

import type {
  ExternalAgentAuthMode,
  ExternalAgentConfig,
  ExternalAgentPermissionMode,
  ExternalAgentType,
} from '@/services/config';
import type { ExternalAgentAuthStatus } from '@/services/external-agents';

export const DEFAULT_AGENT: ExternalAgentConfig = {
  name: '',
  type: 'cli',
  command: '',
  args: [],
  enabled: true,
  permissionMode: 'safe',
  authMode: 'subscription',
  description: '',
  maxTurns: 25,
};

const KNOWN_BACKENDS = ['claude', 'codex', 'gemini', 'qwen'] as const;

/** The executable's name wherever the command lives and whatever its extension: `C:\tools\gemini.cmd` is `gemini`. */
function executableName(command: string): string {
  return (command.split(/[\\/]/).pop() ?? '').toLowerCase().split('.')[0];
}

/** The known agent a command exactly is. A wrapper or adapter that merely carries an agent's name is a command of its own. */
export function knownBackendOfCommand(command: string): string | null {
  const name = executableName(command);
  return KNOWN_BACKENDS.find((backend) => backend === name) ?? null;
}

// The server owns the rule; this mirrors it for immediate feedback while typing.
const ACCOUNT_NAME = /^[a-z0-9][a-z0-9_-]{0,31}$/;

export const isValidAccountName = (name: string): boolean => ACCOUNT_NAME.test(name);

/**
 * The agent an account can be chosen for, or null. An account is a subscription login of a known agent, so an
 * agent on an API key, a command the app does not know and a deployment without subscription login have none.
 */
export function accountBackend(
  command: string,
  authMode: ExternalAgentAuthMode | undefined,
  subscriptionLoginAllowed: boolean,
): string | null {
  return effectiveAuthMode(authMode, subscriptionLoginAllowed) === 'subscription'
    ? knownBackendOfCommand(command)
    : null;
}

/** The agents with the account taken off those that ran as `name` of `backend`; the same list when none did. */
export function detachAccount(
  agents: readonly ExternalAgentConfig[],
  backend: string,
  name: string,
): readonly ExternalAgentConfig[] {
  const uses = (agent: ExternalAgentConfig): boolean =>
    agent.account === name && knownBackendOfCommand(agent.command) === backend;
  if (!agents.some(uses)) {
    return agents;
  }
  return agents.map((agent) => {
    if (!uses(agent)) {
      return agent;
    }
    const detached = { ...agent };
    delete detached.account;
    return detached;
  });
}

/** The agent a command's login belongs to: the agent it is, else one whose name it carries (`claude-code`). */
export function backendKeyFromCommand(command: string): string | null {
  return knownBackendOfCommand(command) ?? KNOWN_BACKENDS.find((b) => executableName(command).includes(b)) ?? null;
}

/**
 * The transport a command always runs over when it is exactly a known agent, whatever type was stored for it;
 * null for any other command, and until the server has reported.
 */
export function contractTransport(
  command: string,
  statuses: readonly ExternalAgentAuthStatus[],
): ExternalAgentType | null {
  const backend = knownBackendOfCommand(command);
  return statuses.find((status) => status.backend === backend)?.transport ?? null;
}

export const AGENT_PRESETS: { key: string; config: ExternalAgentConfig }[] = [
  {
    key: 'claude',
    config: {
      ...DEFAULT_AGENT,
      name: 'claude-code',
      command: 'claude',
      args: ['-p', '--output-format', 'stream-json'],
      description: 'Full-stack coding agent powered by Anthropic Claude',
    },
  },
  {
    key: 'codex',
    config: {
      ...DEFAULT_AGENT,
      name: 'codex-cli',
      type: 'app_server',
      command: 'codex',
      args: [],
      description: 'OpenAI Codex CLI coding agent (requires Responses API provider)',
    },
  },
  {
    key: 'gemini',
    config: {
      ...DEFAULT_AGENT,
      name: 'gemini-cli',
      type: 'acp',
      command: 'gemini',
      args: [],
      description: 'Google Gemini-powered coding agent',
    },
  },
  {
    key: 'qwen',
    config: {
      ...DEFAULT_AGENT,
      name: 'qwen-code',
      type: 'acp',
      command: 'qwen',
      args: [],
      description: 'Alibaba Qwen-powered coding agent',
    },
  },
];

/** The permission modes every agent offers. */
export const PERMISSION_MODE_OPTIONS: { value: ExternalAgentPermissionMode; labelKey: string }[] = [
  { value: 'allow_all', labelKey: 'permissionAllowAll' },
  { value: 'safe', labelKey: 'permissionSafe' },
];

/** The command-line agents whose own permission flags can be set per mode, and that can ask the user. */
const CLI_BACKENDS_THAT_ASK = new Set(['claude']);

/**
 * The hint under the permission select: what `safe` means for this agent. An agent that talks to the app over ACP or its
 * own app server asks the user; a command-line agent asks only when it is one we know; a command we do not know has no
 * flags to set, so it keeps its own behaviour.
 */
export function permissionHintKey(type: ExternalAgentType, command: string): string {
  if (type === 'acp' || type === 'app_server') {
    return 'permissionHintAsk';
  }
  const backend = backendKeyFromCommand(command);
  return backend !== null && CLI_BACKENDS_THAT_ASK.has(backend) ? 'permissionHintAsk' : 'permissionHintUnrestricted';
}

/** Coerce a stored mode to one on offer; a name of an older vocabulary falls back to `safe` (fail-closed). */
export function normalizePermissionMode(mode: string): ExternalAgentPermissionMode {
  return mode === 'allow_all' ? 'allow_all' : 'safe';
}

/** One line of the environment editor. Blank and unfinished lines exist only while the form is open. */
export interface EnvRow {
  id: string;
  name: string;
  value: string;
}

/** An agent being edited: its environment is a list of lines, so a line being typed can be shown as it is. */
export type ExternalAgentDraft = Omit<ExternalAgentConfig, 'env'> & { envRows: EnvRow[] };

let nextEnvRowId = 0;

export function newEnvRow(name = '', value = ''): EnvRow {
  nextEnvRowId += 1;
  return { id: `env-row-${nextEnvRowId}`, name, value };
}

export function toDraft(agent: ExternalAgentConfig): ExternalAgentDraft {
  const { env, ...rest } = agent;
  return { ...rest, envRows: Object.entries(env ?? {}).map(([name, value]) => newEnvRow(name, value)) };
}

export type EnvRowProblem = 'missing' | 'invalid' | 'duplicate';

// A name outside this shape cannot be put into a process environment: spawning the agent fails on it.
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

const isBlankRow = (row: EnvRow): boolean => row.name.trim() === '' && row.value === '';

/** What is wrong with each line, by row id. A blank line is left out of the agent and is never wrong. */
export function envRowProblems(rows: readonly EnvRow[]): Map<string, EnvRowProblem> {
  const problems = new Map<string, EnvRowProblem>();
  const taken = new Set<string>();
  for (const row of rows) {
    if (isBlankRow(row)) {
      continue;
    }
    const name = row.name.trim();
    if (!name) {
      problems.set(row.id, 'missing');
    } else if (!ENV_NAME.test(name)) {
      problems.set(row.id, 'invalid');
    } else if (taken.has(name)) {
      problems.set(row.id, 'duplicate');
    } else {
      taken.add(name);
    }
  }
  return problems;
}

/** The auth mode the form shows and stores: a deployment without subscription login only has the API key. */
export function effectiveAuthMode(
  authMode: ExternalAgentAuthMode | undefined,
  subscriptionLoginAllowed: boolean,
): ExternalAgentAuthMode {
  return subscriptionLoginAllowed ? (authMode ?? 'subscription') : 'api_key';
}

/**
 * The agent as it is stored: trimmed text, the transport its contract fixes (`contractTransport`) in place of a
 * stale choice, no blank arguments, a permission mode its type can honour (otherwise switching type could persist
 * a mode the form never showed), the auth mode the deployment supports, an `account` only where one can be chosen
 * (`accountBackend`), and an `env` only when it has entries.
 * The environment lines must have passed `envRowProblems`.
 */
export function cleanAgent(
  draft: ExternalAgentDraft,
  subscriptionLoginAllowed = true,
  fixedTransport: ExternalAgentType | null = null,
): ExternalAgentConfig {
  const { envRows, account, ...rest } = draft;
  const env = Object.fromEntries(envRows.filter((row) => !isBlankRow(row)).map((row) => [row.name.trim(), row.value]));
  const cleaned: ExternalAgentConfig = {
    ...rest,
    type: fixedTransport ?? draft.type,
    name: draft.name.trim(),
    command: draft.command.trim(),
    args: (draft.args ?? []).filter((a) => a.trim() !== ''),
    permissionMode: normalizePermissionMode(draft.permissionMode),
  };
  if (!subscriptionLoginAllowed) {
    cleaned.authMode = 'api_key';
  }
  if (account && accountBackend(cleaned.command, draft.authMode, subscriptionLoginAllowed) !== null) {
    cleaned.account = account;
  }
  if (Object.keys(env).length > 0) {
    cleaned.env = env;
  }
  return cleaned;
}
