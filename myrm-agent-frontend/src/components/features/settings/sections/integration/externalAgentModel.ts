/**
 * [INPUT]
 * - @/services/config::ExternalAgentConfig (POS: one stored external agent entry)
 * - @/services/external-agents::ExternalAgentContract (POS: the server's catalog entry for each known agent: what it is called and how it starts when added, its transport, whether the deployment runs it)
 *
 * [OUTPUT]
 * - DEFAULT_AGENT, AgentPreset, availablePresets: the blank entry, and the one-click presets the known agents the
 *   deployment runs give the add form
 * - PERMISSION_MODE_OPTIONS, permissionHintKey, normalizePermissionMode: the permission modes on offer and what each
 *   means for the agent being edited
 * - knownBackendOfCommand, backendKeyFromCommand: command → the known agent it is, command → the agent its login belongs to
 * - isValidAccountName, accountBackend, detachAccount: the account names the server accepts, the agent an account applies
 *   to, and the agents left after an account is removed
 * - memoryNeedsTrustedFolder: whether the agent only uses shared memory in folders it has been told to trust
 * - contractTransport: the transport a known agent's contract fixes for a command
 * - ExternalAgentDraft, EnvRow, toDraft, newEnvRow, envRowProblems, effectiveAuthMode, cleanAgent: the editable
 *   form of an agent and the rules for turning it back into what is stored
 *
 * [POS]
 * Pure data and rules of the external agent editor; no React, so every rule is unit-testable. Which agents are known
 * and what each is comes from the server's catalog (the harness registry), never from a list kept here.
 */

import type {
  ExternalAgentAuthMode,
  ExternalAgentConfig,
  ExternalAgentPermissionMode,
  ExternalAgentType,
} from '@/services/config';
import type { ExternalAgentContract } from '@/services/external-agents';

/** The server's catalog: one entry per known agent, in the order the registry lists them; empty until the server has answered. */
type KnownAgents = readonly ExternalAgentContract[];

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

/** The executable's name wherever the command lives and whatever its extension: `C:\tools\gemini.cmd` is `gemini`. */
function executableName(command: string): string {
  return (command.split(/[\\/]/).pop() ?? '').toLowerCase().split('.')[0];
}

/** Anything that names an agent by its backend: an entry of the catalog, or the status row the server reports for it. */
interface NamedAgent {
  readonly backend: string;
}

/** The agent a command exactly is. A wrapper or adapter that merely carries an agent's name is a command of its own. */
function agentOfCommand<T extends NamedAgent>(command: string, agents: readonly T[]): T | undefined {
  const name = executableName(command);
  return agents.find((agent) => agent.backend === name);
}

/** The known agent a command exactly is. */
export function knownBackendOfCommand(command: string, known: KnownAgents): string | null {
  return agentOfCommand(command, known)?.backend ?? null;
}

/**
 * Whether the agent connects to no MCP server in a folder it has not been told to trust (Gemini CLI). The decision is
 * made in the agent and nowhere else: the app cannot make it for the user, so the form says so next to the memory
 * switch.
 */
export const memoryNeedsTrustedFolder = (command: string, known: KnownAgents): boolean =>
  agentOfCommand(command, known)?.mcpNeedsTrustedFolder === true;

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
  known: KnownAgents,
): string | null {
  return effectiveAuthMode(authMode, subscriptionLoginAllowed) === 'subscription'
    ? knownBackendOfCommand(command, known)
    : null;
}

/** The agents with the account taken off those that ran as `name` of `backend`; the same list when none did. */
export function detachAccount(
  agents: readonly ExternalAgentConfig[],
  backend: string,
  name: string,
  known: KnownAgents,
): readonly ExternalAgentConfig[] {
  const uses = (agent: ExternalAgentConfig): boolean =>
    agent.account === name && knownBackendOfCommand(agent.command, known) === backend;
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

/**
 * The agent a command's login belongs to: the agent it is, else one whose name it carries (`claude-code`). The login
 * state comes in its own rows, so `agents` may be those rows and the answer does not wait for the catalog.
 */
export function backendKeyFromCommand(command: string, agents: readonly NamedAgent[]): string | null {
  const name = executableName(command);
  return (agentOfCommand(command, agents) ?? agents.find((agent) => name.includes(agent.backend)))?.backend ?? null;
}

/**
 * The transport a command always runs over when it is exactly a known agent, whatever type was stored for it;
 * null for any other command, and until the server has answered.
 */
export function contractTransport(command: string, known: KnownAgents): ExternalAgentType | null {
  return agentOfCommand(command, known)?.transport ?? null;
}

export interface AgentPreset {
  key: string;
  label: string;
  config: ExternalAgentConfig;
}

/**
 * One preset per known agent the deployment runs, each starting as the agent's own contract says (its transport, name
 * and description). The arguments stay empty: the launch arguments of a known agent are resolved when it starts.
 */
export function availablePresets(known: KnownAgents): AgentPreset[] {
  return known
    .filter((agent) => agent.availableInDeployment)
    .map((agent) => ({
      key: agent.backend,
      label: agent.title,
      config: {
        ...DEFAULT_AGENT,
        name: agent.defaultName,
        type: agent.transport,
        command: agent.backend,
        description: agent.description,
      },
    }));
}

/** The permission modes every agent offers. */
export const PERMISSION_MODE_OPTIONS: { value: ExternalAgentPermissionMode; labelKey: string }[] = [
  { value: 'allow_all', labelKey: 'permissionAllowAll' },
  { value: 'safe', labelKey: 'permissionSafe' },
];

/**
 * The hint under the permission select: what `safe` means for this agent. An agent that talks to the app over ACP or its
 * own app server asks the user; a command-line agent asks only when its contract sets the flags that make it ask, which
 * every known agent driven as a command line does; a command we do not know has no flags to set, so it keeps its own
 * behaviour.
 */
export function permissionHintKey(type: ExternalAgentType, command: string, known: KnownAgents): string {
  if (type === 'acp' || type === 'app_server') {
    return 'permissionHintAsk';
  }
  const backend = backendKeyFromCommand(command, known);
  return known.find((agent) => agent.backend === backend)?.transport === 'cli'
    ? 'permissionHintAsk'
    : 'permissionHintUnrestricted';
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
 * (`accountBackend`), `shareMemory` only as the explicit `true` of a consent, and an `env` only when it has entries.
 * The environment lines must have passed `envRowProblems`.
 */
export function cleanAgent(
  draft: ExternalAgentDraft,
  known: KnownAgents,
  subscriptionLoginAllowed = true,
): ExternalAgentConfig {
  const { envRows, account, shareMemory, ...rest } = draft;
  const env = Object.fromEntries(envRows.filter((row) => !isBlankRow(row)).map((row) => [row.name.trim(), row.value]));
  const cleaned: ExternalAgentConfig = {
    ...rest,
    type: contractTransport(draft.command, known) ?? draft.type,
    name: draft.name.trim(),
    command: draft.command.trim(),
    args: (draft.args ?? []).filter((a) => a.trim() !== ''),
    permissionMode: normalizePermissionMode(draft.permissionMode),
  };
  if (!subscriptionLoginAllowed) {
    cleaned.authMode = 'api_key';
  }
  if (account && accountBackend(cleaned.command, draft.authMode, subscriptionLoginAllowed, known) !== null) {
    cleaned.account = account;
  }
  if (shareMemory === true) {
    cleaned.shareMemory = true;
  }
  if (Object.keys(env).length > 0) {
    cleaned.env = env;
  }
  return cleaned;
}
