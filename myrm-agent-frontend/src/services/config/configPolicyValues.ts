/**
 * [INPUT]
 * - none (self-contained declarations)
 *
 * [OUTPUT]
 * The value shapes of the external-agent, permission and security, budget, backup, security-dashboard, browser and
 * web-fetch settings.
 *
 * [POS]
 * Runtime-policy half of the config value shapes: how the agent may act and which outside services it may reach.
 * Self-contained, so `./configValues` can build `ConfigValueMap` on it; `./types` re-exports it.
 */

/**
 * External Agent runtime type.
 */
export type ExternalAgentType = 'cli' | 'acp';

/**
 * External Agent permission mode.
 *
 * - `safe`: the agent may not change anything without the user's say-so (an agent that can ask does, one that
 *   cannot runs read-only).
 * - `allow_all`: the agent acts unattended.
 */
export type ExternalAgentPermissionMode = 'allow_all' | 'safe';

/**
 * External Agent authentication mode.
 *
 * - `subscription`: delegate runs on the user's own model plan via the CLI's
 *   own login state; the host strips provider API keys from the child env.
 * - `api_key`: the host injects provider API keys from the agent's `env`.
 */
export type ExternalAgentAuthMode = 'subscription' | 'api_key';

/**
 * Single external agent configuration.
 */
export interface ExternalAgentConfig {
  name: string;
  type: ExternalAgentType;
  command: string;
  args?: string[];
  enabled: boolean;
  permissionMode: ExternalAgentPermissionMode;
  authMode?: ExternalAgentAuthMode;
  description?: string;
  maxTurns?: number;
  /** Variables set when the agent starts, such as its API key; the values are secrets. */
  env?: Record<string, string>;
}

/**
 * ExternalAgents configuration value stored via ConfigSyncManager.
 */
export interface ExternalAgentsConfigValue {
  agents: ExternalAgentConfig[];
}

/**
 * Permission Engine — tool execution access control.
 */
export type PermissionAction = 'allow' | 'ask' | 'deny';

export interface PermissionRuleConfig {
  permission: string;
  pattern: string;
  action: PermissionAction;
}

export interface PathPolicyConfig {
  allowedRoots?: string[];
}

export interface SecurityConfigValue {
  permissions: Record<string, PermissionAction | Record<string, PermissionAction>>;
  approvalTimeoutSeconds: number;
  approvalTimeoutBehavior?: 'deny' | 'allow';
  capabilityMatrix?: Record<string, PermissionAction>;
  pathPolicy?: PathPolicyConfig;
  networkAllowlist?: string[];
  networkBlocklist?: string[];
  commandDenylist?: string[];
  domainHitlEnabled?: boolean;
  injectionPolicy?: 'log_only' | 'fail_closed';
  planConfirmEnabled?: boolean;
  yoloModeEnabled?: boolean;
  yoloModeEnabledAt?: number;
  yoloModeTimeout?: number;
  autoReviewEnabled?: boolean;
  autoReviewModel?: string;
}

export interface BudgetPolicyConfigValue {
  enabled: boolean;
  daily_limit_usd: number | null;
  session_limit_usd: number | null;
  per_call_limit_usd: number | null;
  warning_threshold: number;
  finalization_reserve_pct: number;
  action_on_exceeded: 'warn' | 'block' | 'finalize';
}

export interface BackupSyncConfigValue {
  enabled: boolean;
  provider: 'webdav' | 's3';
  autoSync: boolean;
  syncInterval: number;
  maxBackups: number;
  deviceName: string;
  webdav: {
    host: string;
    username: string;
    password: string;
    path: string;
  };
  s3: {
    endpoint: string;
    region: string;
    bucket: string;
    accessKeyId: string;
    secretAccessKey: string;
    prefix: string;
    forcePathStyle: boolean;
  };
}

export interface SecurityDashboardSettingsConfigValue {
  monitoredGithubRepos: string[];
}

export type BrowserCloudProviderType = 'browserbase' | 'browserless' | 'notte' | 'custom';

export interface BrowserCloudProviderConfigValue {
  enabled: boolean;
  provider: BrowserCloudProviderType;
  credential: string;
  custom_ws_url: string;
}

export interface BrowserProxyConfigValue {
  enabled: boolean;
  proxies: string[];
}

export interface CaptchaSolverConfigValue {
  enabled: boolean;
  api_key: string;
}

export interface WebFetchFirecrawlConfigValue {
  inheritFromSearch: boolean;
  api_key: string | null;
  apiBase: string | null;
  networkIsolationConfirmed?: boolean;
}

export interface WebFetchEscalationConfigValue {
  enabled: boolean;
  jinaApiKey: string | null;
  firecrawl: WebFetchFirecrawlConfigValue;
  sessionCap: number;
}
