/**
 * [INPUT]
 * - ./protocol (POS: JSON-RPC envelope, params checks, BridgeError)
 * - ./declaration::CapabilityStatus (POS: whether a declared capability may be used)
 *
 * [OUTPUT]
 * - AppBridgeDeps / AppBridgeActivity / ProxyFetchRequest / ProxyFetchReply: what the host supplies and what it sends on.
 * - createAppBridgeHost: returns the handler that turns one page message into a reply (or `null` for a foreign message).
 *
 * [POS]
 * Host half of the channel, free of React and of the DOM so every path is testable: capability gate, per-method call
 * limiter, parameter checks, then the notification or the proxied fetch. Who sent the message is decided by the caller
 * (`useAppBridge`); everything here treats the message as untrusted page input.
 */

import type { CapabilityStatus } from './declaration';
import {
  BridgeError,
  parseFetchParams,
  parseNotifyParams,
  parseRpcRequest,
  rpcFailure,
  rpcResult,
  type BridgeCapability,
  type BridgeFailure,
  type FetchParams,
  type RpcResponse,
} from './protocol';

export interface AppBridgeActivity {
  capability: BridgeCapability;
  outcome: 'ok' | 'denied' | 'rate_limited' | 'error';
  /** A host name only; never a path, query or body. */
  detail: string | null;
}

export interface ProxyFetchRequest extends FetchParams {
  version_sha256: string;
}

export interface ProxyFetchReply {
  ok: boolean;
  status: number | null;
  headers: Record<string, string>;
  body: string;
  code: string | null;
  message: string | null;
}

export interface AppBridgeDeps {
  appName: string;
  versionSha256: string;
  getStatuses: () => Record<BridgeCapability, CapabilityStatus>;
  proxyFetch: (request: ProxyFetchRequest) => Promise<ProxyFetchReply>;
  /** Fire-and-forget log of what the host itself decided; calls the server proxies are logged by the server. */
  reportActivity: (activity: AppBridgeActivity) => void;
  showNotification: (title: string, body: string) => Promise<void>;
  now?: () => number;
}

const WINDOW_MS = 60_000;
/** Above the server's own fetch limit so the server stays the arbiter for allowed calls; this one bounds denied-call chatter. */
const CALL_LIMITS: Record<BridgeCapability, number> = { notify: 5, fetch: 120 };

const SERVER_FAILURES: readonly BridgeFailure[] = [
  'rate_limited',
  'host_not_granted',
  'bad_request',
  'blocked',
  'response_too_large',
  'timeout',
  'upstream_error',
];

function toFailure(code: string | null): BridgeFailure {
  return SERVER_FAILURES.find((known) => known === code) ?? 'failed';
}

function hostLabel(url: string): string | null {
  try {
    return new URL(url).hostname.toLowerCase();
  } catch {
    return null;
  }
}

type Admission = 'admit' | 'first_over' | 'over';

/** Sliding window per capability; `first_over` lets the caller log one refusal per window instead of one per call. */
function createLimiter(now: () => number) {
  const calls: Record<BridgeCapability, number[]> = { notify: [], fetch: [] };
  const refused: Record<BridgeCapability, boolean> = { notify: false, fetch: false };
  return (capability: BridgeCapability): Admission => {
    const cutoff = now() - WINDOW_MS;
    const recent = calls[capability].filter((at) => at > cutoff);
    if (recent.length >= CALL_LIMITS[capability]) {
      calls[capability] = recent;
      const first = !refused[capability];
      refused[capability] = true;
      return first ? 'first_over' : 'over';
    }
    recent.push(now());
    calls[capability] = recent;
    refused[capability] = false;
    return 'admit';
  };
}

export function createAppBridgeHost(deps: AppBridgeDeps): (data: unknown) => Promise<RpcResponse | null> {
  const now = deps.now ?? Date.now;
  const admit = createLimiter(now);

  async function gate(capability: BridgeCapability, detail: string | null): Promise<void> {
    const admission = admit(capability);
    if (admission !== 'admit') {
      if (admission === 'first_over') {
        deps.reportActivity({ capability, outcome: 'rate_limited', detail });
      }
      throw new BridgeError('rate_limited', 'Too many calls; slow down');
    }
    const status = deps.getStatuses()[capability];
    if (status === 'allowed') {
      return;
    }
    deps.reportActivity({ capability, outcome: 'denied', detail });
    throw status === 'undeclared'
      ? new BridgeError('not_declared', `${capability} is not declared by this app`)
      : new BridgeError('permission_denied', `${capability} is not allowed for this app`);
  }

  async function notify(params: unknown): Promise<null> {
    await gate('notify', null);
    const parsed = parseNotifyParams(params);
    if (!parsed) {
      throw new BridgeError('invalid_params', 'notify needs a title (up to 80 characters) and an optional body');
    }
    try {
      await deps.showNotification(`${deps.appName} · ${parsed.title}`, parsed.body);
    } catch {
      deps.reportActivity({ capability: 'notify', outcome: 'error', detail: null });
      throw new BridgeError('failed', 'The notification could not be shown');
    }
    deps.reportActivity({ capability: 'notify', outcome: 'ok', detail: null });
    return null;
  }

  async function fetchThroughHost(params: unknown): Promise<Pick<ProxyFetchReply, 'status' | 'headers' | 'body'>> {
    const parsed = parseFetchParams(params);
    await gate('fetch', parsed ? hostLabel(parsed.url) : null);
    if (!parsed) {
      throw new BridgeError('invalid_params', 'fetch needs a url and optionally a method, headers and body');
    }
    let reply: ProxyFetchReply;
    try {
      reply = await deps.proxyFetch({ ...parsed, version_sha256: deps.versionSha256 });
    } catch {
      throw new BridgeError('failed', 'The request could not be sent');
    }
    if (!reply.ok) {
      throw new BridgeError(toFailure(reply.code), reply.message ?? 'The request was refused');
    }
    return { status: reply.status, headers: reply.headers, body: reply.body };
  }

  return async (data) => {
    const request = parseRpcRequest(data);
    if (!request) {
      return null;
    }
    try {
      if (request.method === 'notify') {
        return rpcResult(request.id, await notify(request.params));
      }
      if (request.method === 'fetch') {
        return rpcResult(request.id, await fetchThroughHost(request.params));
      }
      throw new BridgeError('method_not_found', `Unknown method: ${request.method.slice(0, 40)}`);
    } catch (error) {
      return rpcFailure(
        request.id,
        error instanceof BridgeError ? error : new BridgeError('failed', 'Unexpected error'),
      );
    }
  };
}
