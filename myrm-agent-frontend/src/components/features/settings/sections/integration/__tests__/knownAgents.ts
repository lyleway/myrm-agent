import type { ExternalAgentType } from '@/services/config';
import type {
  ExternalAgentAuthStatus,
  ExternalAgentContract,
  ExternalAgentLoginStrategy,
} from '@/services/external-agents';

export type KnownBackend = 'claude' | 'codex' | 'gemini' | 'qwen';

/**
 * The catalog the server answers for the four agents the harness registry knows today, in the registry's order. The
 * registry is the source of every field here; the tests only need entries shaped like the real ones.
 */
const CONTRACTS: readonly ExternalAgentContract[] = [
  {
    backend: 'claude',
    title: 'Claude Code',
    defaultName: 'claude-code',
    description: 'Full-stack coding agent powered by Anthropic Claude',
    transport: 'cli',
    mcpNeedsTrustedFolder: false,
    availableInDeployment: true,
  },
  {
    backend: 'codex',
    title: 'Codex CLI',
    defaultName: 'codex-cli',
    description: 'OpenAI Codex CLI coding agent (requires Responses API provider)',
    transport: 'app_server',
    mcpNeedsTrustedFolder: false,
    availableInDeployment: true,
  },
  {
    backend: 'gemini',
    title: 'Gemini CLI',
    defaultName: 'gemini-cli',
    description: 'Google Gemini-powered coding agent',
    transport: 'acp',
    mcpNeedsTrustedFolder: true,
    availableInDeployment: true,
  },
  {
    backend: 'qwen',
    title: 'Qwen Code',
    defaultName: 'qwen-code',
    description: 'Alibaba Qwen-powered coding agent',
    transport: 'acp',
    mcpNeedsTrustedFolder: false,
    availableInDeployment: true,
  },
];

const LOGIN: Record<KnownBackend, ExternalAgentLoginStrategy> = {
  claude: 'terminal',
  codex: 'browser_oauth',
  gemini: 'terminal',
  qwen: 'terminal',
};

/** The catalog entry of one known agent, with what a test wants different. */
export function contract(backend: KnownBackend, overrides: Partial<ExternalAgentContract> = {}): ExternalAgentContract {
  const found = CONTRACTS.find((candidate) => candidate.backend === backend);
  if (found === undefined) {
    throw new Error(`no catalog entry for ${backend}`);
  }
  return { ...found, ...overrides };
}

/** The catalog of the four known agents, with what a test wants different in every one of them. */
export function knownContracts(overrides: Partial<ExternalAgentContract> = {}): ExternalAgentContract[] {
  return CONTRACTS.map((entry) => ({ ...entry, ...overrides }));
}

/** An entry of an agent the registry does not list today: what the editor does for a new one without a code change. */
export function newContract(
  backend: string,
  transport: ExternalAgentType,
  overrides: Partial<ExternalAgentContract> = {},
): ExternalAgentContract {
  return {
    ...contract('claude'),
    backend,
    title: `${backend} agent`,
    defaultName: `${backend}-cli`,
    description: `The ${backend} agent`,
    transport,
    ...overrides,
  };
}

/**
 * The status row of one known agent on a machine where it is installed and nobody is signed in, with what a test
 * wants different.
 */
export function agentRow(
  backend: KnownBackend,
  overrides: Partial<ExternalAgentAuthStatus> = {},
): ExternalAgentAuthStatus {
  return {
    backend,
    installed: true,
    path: `/usr/bin/${backend}`,
    version: '1.0.0',
    transport: contract(backend).transport,
    authenticated: false,
    authStatus: 'not_authenticated',
    loginStrategy: LOGIN[backend],
    subscriptionLoginAllowed: true,
    availableInDeployment: true,
    ...overrides,
  };
}
