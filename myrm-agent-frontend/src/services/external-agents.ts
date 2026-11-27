/**
 * [INPUT]
 * - @/lib/api::apiRequest, fetchWithTimeout (POS: 前端统一请求入口)
 *
 * [OUTPUT]
 * - ExternalAgentAuthStatus / ExternalAgentAuthEvent 类型
 * - ExternalAgentAccount 类型
 * - getExternalAgentAuthStatus / streamExternalAgentInstall / streamExternalAgentLogin
 *   / feedExternalAgentLogin / importExternalAgentCredential / logoutExternalAgent
 * - listExternalAgentAccounts / createExternalAgentAccount / deleteExternalAgentAccount
 *
 * [POS]
 * 外部委托 Agent 订阅鉴权前端服务层。封装登录状态查询、SSE 安装与交互式登录
 * （共用一个 SSE 解析器，非 2xx 响应抛错而不是静默结束）、凭据导入/登出，
 * 命名账号的列出、创建、删除，供设置页的鉴权 UI 调用（local + SaaS 全模式可用）。
 * 登录、导入、登出可带账号名，作用于该账号目录里的登录而不是 CLI 自己的。
 */
import { apiRequest, fetchWithTimeout } from '@/lib/api';
import type { ExternalAgentType } from '@/services/config';

export type ExternalAgentLoginStrategy = 'browser_oauth' | 'paste_code' | 'manual_import';

export interface ExternalAgentAuthStatus {
  backend: string;
  installed: boolean;
  path: string | null;
  version: string | null;
  /** How the app runs this agent. It is the agent's own contract, so a stored choice of type never changes it. */
  transport: ExternalAgentType;
  authenticated: boolean;
  authStatus: 'authenticated' | 'not_authenticated' | 'unknown';
  loginStrategy: ExternalAgentLoginStrategy;
  scriptableLogin: boolean;
  needsCodeInput: boolean;
  /** False in a hosted deployment, which runs delegated agents on an API key and never keeps a subscription login. */
  subscriptionLoginAllowed: boolean;
}

export interface ExternalAgentAuthEvent {
  type: 'status' | 'prompt' | 'success' | 'error' | 'progress';
  message: string;
  url?: string;
  code?: string;
}

interface ExternalAgentCredentialState {
  backend: string;
  authenticated: boolean;
  authStatus: string;
}

/** One named login of a delegated CLI, kept in a home of its own. */
export interface ExternalAgentAccount {
  backend: string;
  name: string;
  authenticated: boolean;
  authStatus: ExternalAgentAuthStatus['authStatus'];
}

/** Fetch install + login state for every known delegated backend (drives badges). */
export async function getExternalAgentAuthStatus(): Promise<ExternalAgentAuthStatus[]> {
  const res = await apiRequest<{ backends: ExternalAgentAuthStatus[] }>('/external-agents/auth/status', {
    silent: true,
  });
  return res.backends ?? [];
}

/**
 * Match a configured command to the server's status row for the same backend.
 *
 * Receives only a non-empty, path-free command (the caller handles explicit paths), so
 * this compares the bare executable name — minus any extension like `.cmd` / `.exe`.
 * Returns undefined for a name the server does not track, which callers treat as unknown.
 */
function resolveConfiguredBackendStatus(
  command: string,
  statuses: ReadonlyArray<ExternalAgentAuthStatus>,
): ExternalAgentAuthStatus | undefined {
  const executable = command.toLowerCase().replace(/\.[^.]+$/, '');
  return statuses.find((row) => executable === row.backend.toLowerCase());
}

/**
 * Delegation readiness, split so the UI can tell "nothing configured" apart from
 * "configured but not installed" — two different problems needing two different fixes.
 */
export type ExternalCliReadiness = 'not_configured' | 'unavailable' | 'ready';

/** True when auth/status reports any backend installed on this machine (PATH auto-detect). */
export function hasAutoDetectedExternalCliBackend(statuses: ReadonlyArray<ExternalAgentAuthStatus>): boolean {
  return statuses.some((row) => row.installed);
}

/**
 * Whether external CLI delegation can run, and if not, why.
 *
 * A bare command name is resolved through `PATH`, so the server's detection decides
 * whether it exists. A command the server does not track (a custom wrapper) or an
 * explicit path the user pinned is not something the server can confirm, so that
 * explicit configuration is trusted instead of reported as missing.
 */
export function resolveExternalCliReadiness(
  agents: ReadonlyArray<{ enabled?: boolean; command?: string }> | undefined,
  statuses: ReadonlyArray<ExternalAgentAuthStatus>,
  localMode: boolean,
): ExternalCliReadiness {
  const configured = (agents ?? [])
    .filter((agent) => agent.enabled !== false)
    .map((agent) => agent.command?.trim())
    .filter((command): command is string => Boolean(command));
  if (configured.length === 0) {
    return localMode && hasAutoDetectedExternalCliBackend(statuses) ? 'ready' : 'not_configured';
  }
  const resolvable = configured.some((command) => {
    if (/[\\/]/.test(command)) {
      // An explicit path is user-pinned; PATH-based detection says nothing about it.
      return true;
    }
    const status = resolveConfiguredBackendStatus(command, statuses);
    return status ? status.installed : true;
  });
  return resolvable ? 'ready' : 'unavailable';
}

/** `fetchWithTimeout` (used for SSE) does not attach the bearer token the way `apiRequest` does. */
function withBearerToken(headers: Record<string, string>): Record<string, string> {
  const token = typeof window !== 'undefined' ? localStorage.getItem('auth_token') : null;
  return token ? { ...headers, Authorization: `Bearer ${token}` } : headers;
}

/**
 * Parse an SSE body into events. Malformed frames are skipped; `[DONE]` ends the stream.
 * Leaving early (consumer stops or the request is aborted) closes the underlying connection.
 */
async function* readSseEvents(body: ReadableStream<Uint8Array>): AsyncGenerator<ExternalAgentAuthEvent, void, unknown> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        return;
      }
      buffer += decoder.decode(value, { stream: true });
      let sep: number;
      while ((sep = buffer.indexOf('\n\n')) >= 0) {
        const frame = buffer.slice(0, sep);
        buffer = buffer.slice(sep + 2);
        const dataLine = frame.split('\n').find((line) => line.startsWith('data:'));
        if (!dataLine) {
          continue;
        }
        const data = dataLine.slice(5).trim();
        if (data === '[DONE]') {
          return;
        }
        try {
          yield JSON.parse(data) as ExternalAgentAuthEvent;
        } catch {
          // ignore malformed frame
        }
      }
    }
  } finally {
    reader.cancel().catch(() => undefined);
  }
}

/**
 * Install a backend CLI into the managed toolchain, yielding the installer's progress, then `success` or `error`.
 * The server bounds every install step itself, so no client-side deadline applies; abort the signal to cancel.
 */
export async function* streamExternalAgentInstall(
  backend: string,
  signal?: AbortSignal,
): AsyncGenerator<ExternalAgentAuthEvent, void, unknown> {
  const response = await fetchWithTimeout(
    `/external-agents/install/${encodeURIComponent(backend)}`,
    { method: 'POST', headers: withBearerToken({ Accept: 'text/event-stream' }), signal },
    0,
  );
  if (!response.ok || !response.body) {
    throw new Error(`Install stream failed: ${response.status}`);
  }
  yield* readSseEvents(response.body);
}

/** Persist a credential blob captured elsewhere (universal fallback), into the named account when one is given. */
export function importExternalAgentCredential(
  backend: string,
  content: string,
  filename?: string,
  account?: string,
): Promise<ExternalAgentCredentialState> {
  return apiRequest<ExternalAgentCredentialState>('/external-agents/auth/import', {
    method: 'POST',
    body: JSON.stringify({ backend, content, filename, account }),
  });
}

/**
 * Log a backend out through its CLI and remove its stored credentials (those of the named account, when one is given).
 * The state it returns is what the CLI reports afterwards: a login that could not be removed is still `authenticated`.
 */
export function logoutExternalAgent(backend: string, account?: string): Promise<ExternalAgentCredentialState> {
  return apiRequest<ExternalAgentCredentialState>('/external-agents/auth/logout', {
    method: 'POST',
    body: JSON.stringify({ backend, account }),
  });
}

/** Every named account with whether it is logged in. */
export async function listExternalAgentAccounts(): Promise<ExternalAgentAccount[]> {
  const res = await apiRequest<{ accounts: ExternalAgentAccount[] }>('/external-agents/accounts', { silent: true });
  return res.accounts ?? [];
}

/** Make a new, empty account. A name that is taken rejects with an `ApiError` of code 409. */
export function createExternalAgentAccount(backend: string, name: string): Promise<{ backend: string; name: string }> {
  return apiRequest<{ backend: string; name: string }>('/external-agents/accounts', {
    method: 'POST',
    body: JSON.stringify({ backend, name }),
    silent: true,
  });
}

/**
 * Remove an account with its login, settings and history. The account's CLI logs out first; when the login still
 * stands afterwards the account is kept and this rejects with an `ApiError` of code 409.
 */
export function deleteExternalAgentAccount(backend: string, name: string): Promise<{ ok: boolean }> {
  return apiRequest<{ ok: boolean }>(
    `/external-agents/accounts/${encodeURIComponent(backend)}/${encodeURIComponent(name)}`,
    { method: 'DELETE', silent: true },
  );
}

/** Forward a user-supplied code to a live login session (paste_code flow). */
export function feedExternalAgentLogin(sessionId: string, text: string): Promise<{ ok: boolean }> {
  return apiRequest<{ ok: boolean }>(`/external-agents/auth/login/${encodeURIComponent(sessionId)}/feed`, {
    method: 'POST',
    body: JSON.stringify({ text }),
  });
}

/**
 * Drive an interactive CLI login over SSE, invoking `onEvent` for each event
 * until the stream closes. Pass an AbortSignal to cancel mid-flight. `remote`: the browser that signs in is on
 * another machine than the CLI, so the CLI is asked for a login that works from there.
 */
export async function streamExternalAgentLogin(
  params: { command: string; backend?: string; sessionId: string; account?: string; remote?: boolean },
  onEvent: (event: ExternalAgentAuthEvent) => void,
  signal?: AbortSignal,
): Promise<void> {
  const response = await fetchWithTimeout(
    '/external-agents/auth/login',
    {
      method: 'POST',
      headers: withBearerToken({ 'Content-Type': 'application/json' }),
      body: JSON.stringify({
        command: params.command,
        backend: params.backend,
        sessionId: params.sessionId,
        account: params.account,
        remote: params.remote,
      }),
      signal,
    },
    0,
  );

  if (!response.ok || !response.body) {
    throw new Error(`Login stream failed: ${response.status}`);
  }

  for await (const event of readSseEvents(response.body)) {
    onEvent(event);
  }
}
