/**
 * [INPUT]
 * - @/lib/api::apiRequest, fetchWithTimeout (POS: 前端统一请求入口)
 *
 * [OUTPUT]
 * - ExternalAgentAuthStatus / ExternalAgentAuthEvent 类型
 * - ExternalAgentAccount / ExternalAgentTerminalLogin 类型
 * - getExternalAgentAuthStatus / streamExternalAgentInstall / streamExternalAgentLogin
 *   / getExternalAgentTerminalLogin / logoutExternalAgent
 * - listExternalAgentAccounts / createExternalAgentAccount / deleteExternalAgentAccount
 * - isBlockedInDeployment / resolveExternalCliReadiness: whether a command names an agent the deployment does not run,
 *   and whether delegation can run at all
 *
 * [POS]
 * 外部委托 Agent 订阅鉴权前端服务层。封装登录状态查询、SSE 安装与交互式登录
 * （共用一个 SSE 解析器，非 2xx 响应抛错而不是静默结束）、用户在自己终端里跑的登录
 * （取官方登录命令并重新检测）、登出，命名账号的列出、创建、删除，供设置页的鉴权 UI
 * 调用（local + SaaS 全模式可用）。前端从不接触凭据：登录由 CLI 完成，这里只读「谁登录了」。
 * 登录、终端登录、登出可带账号名，作用于该账号目录里的登录而不是 CLI 自己的。
 */
import { apiRequest, fetchWithTimeout } from '@/lib/api';
import type { ExternalAgentType } from '@/services/config';

/** Who runs the login: the host drives it (`browser_oauth`), or the user does in a terminal of their own (`terminal`). */
export type ExternalAgentLoginStrategy = 'browser_oauth' | 'terminal';

export type ExternalAgentShell = 'posix' | 'powershell' | 'cmd';

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
  /** False in a hosted deployment, which runs delegated agents on an API key and never keeps a subscription login. */
  subscriptionLoginAllowed: boolean;
  /** False where the deployment does not run this agent at all (a hosted deployment), however it would be signed in. */
  availableInDeployment: boolean;
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

/** The login a user runs in a terminal of their own, and whether it has been made yet. */
export interface ExternalAgentTerminalLogin {
  backend: string;
  account: string | null;
  authenticated: boolean;
  authStatus: ExternalAgentAuthStatus['authStatus'];
  /** The shell of the host's own terminal; the command for it is always among `commands`. */
  defaultShell: ExternalAgentShell;
  /** The command for each shell that can spell it. */
  commands: Partial<Record<ExternalAgentShell, string>>;
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

/** The executable a command starts, lowercased and without extension: `C:\tools\Claude.cmd` is `claude`. */
function executableOf(command: string): string {
  return (command.trim().split(/[\\/]/).pop() ?? '').toLowerCase().split('.')[0];
}

/**
 * Whether the deployment does not run the agent a command names. The server decides which agents those are and reports
 * them in the status rows; a command names an agent when it is its executable or carries its name (`claude-code`),
 * which is how the server matches it too.
 */
export function isBlockedInDeployment(command: string, statuses: ReadonlyArray<ExternalAgentAuthStatus>): boolean {
  const executable = executableOf(command);
  return (
    executable !== '' &&
    statuses.some((row) => !row.availableInDeployment && executable.includes(row.backend.toLowerCase()))
  );
}

/**
 * Delegation readiness, split so the UI can tell "nothing configured" apart from "configured but not installed" and
 * from "configured, but the deployment does not run it" — three different problems needing three different fixes.
 */
export type ExternalCliReadiness = 'not_configured' | 'unavailable' | 'blocked' | 'ready';

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
  const runnable = configured.filter((command) => !isBlockedInDeployment(command, statuses));
  if (runnable.length === 0) {
    return 'blocked';
  }
  const resolvable = runnable.some((command) => {
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

/**
 * The login the user runs in a terminal of their own (the named account's, when one is given), spelled for each shell,
 * and whether it has been made yet. Asking again is the re-check: `authenticated` is what the CLI itself reports.
 */
export function getExternalAgentTerminalLogin(backend: string, account?: string): Promise<ExternalAgentTerminalLogin> {
  return apiRequest<ExternalAgentTerminalLogin>('/external-agents/auth/terminal-login', {
    method: 'POST',
    body: JSON.stringify({ backend, account }),
    silent: true,
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

/**
 * Drive the CLI login of an agent whose login the host can run (`browser_oauth`) over SSE, invoking `onEvent` for
 * each event until the stream closes. Abort the signal to cancel mid-flight: the CLI is stopped with the stream.
 * `remote`: the browser that signs in is on another machine than the CLI, so the CLI is asked for a login that works
 * from there.
 */
export async function streamExternalAgentLogin(
  params: { command: string; backend?: string; account?: string; remote?: boolean },
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
