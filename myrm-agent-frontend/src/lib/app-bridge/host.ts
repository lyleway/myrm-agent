/**
 * [INPUT]
 * - ./protocol (POS: JSON-RPC envelope, params checks, BridgeError)
 * - ./callLimiter (POS: per-capability call pacing)
 * - ./declaration::CapabilityStatus (POS: whether a declared capability may be used)
 *
 * [OUTPUT]
 * - AppBridgeDeps / AppSurfaceDeps / AppBridgeActivity / ProxyFetchRequest / ProxyFetchReply: what the host supplies and sends on.
 * - AppBridgeHost / createAppBridgeHost: `handle` turns one page message into a reply (or `null` for a foreign message);
 *   `revoke` and `dispose` take down what the page set up on the desktop.
 *
 * [POS]
 * Host half of the channel, free of React and of the DOM so every path is testable: capability gate, per-method call
 * limiter, parameter checks, then the notification, the proxied fetch or the desktop surface (tray item, global key
 * combinations, floating window). Who sent the message is decided by the caller (`useAppBridge`); everything here treats
 * the message as untrusted page input. What the page registered on the desktop is tracked here so that a revoked
 * permission, or the page going away, takes it down immediately.
 */

import { createCallLimiter } from './callLimiter';
import type { CapabilityStatus } from './declaration';
import {
  BRIDGE_CAPABILITIES,
  BridgeError,
  isDesktopCapability,
  parseFetchParams,
  parseFloatParams,
  parseNotifyParams,
  parseRpcRequest,
  parseShortcutParams,
  parseTrayParams,
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
  /** A host name, a key combination or `on` / `off` only; never a path, query, body or text the page chose. */
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

/** The desktop window's side of `tray`, `shortcut` and `float`; each call says whether the shell did it. */
export interface AppSurfaceDeps {
  setTray: (title: string, tooltip: string) => Promise<boolean>;
  clearTray: () => Promise<boolean>;
  registerShortcut: (accelerator: string) => Promise<'ok' | 'conflict' | 'failed'>;
  /** `null` releases every combination of this app. */
  unregisterShortcut: (accelerator: string | null) => Promise<boolean>;
  setFloat: (on: boolean) => Promise<boolean>;
}

export interface AppBridgeDeps {
  appName: string;
  versionSha256: string;
  getStatuses: () => Record<BridgeCapability, CapabilityStatus>;
  /** The key combinations the page declared; with `shortcut` allowed, the user approved exactly these. */
  getDeclaredShortcuts: () => readonly string[];
  proxyFetch: (request: ProxyFetchRequest) => Promise<ProxyFetchReply>;
  /** `null` outside the app's own desktop window, where the capabilities that act on the desktop cannot exist. */
  surface: AppSurfaceDeps | null;
  /** Fire-and-forget log of what the host itself decided; calls the server proxies are logged by the server. */
  reportActivity: (activity: AppBridgeActivity) => void;
  showNotification: (title: string, body: string) => Promise<void>;
  now?: () => number;
}

export interface AppBridgeHost {
  handle: (data: unknown) => Promise<RpcResponse | null>;
  /** Takes down what the page set up under `capability` (a no-op for capabilities that leave nothing behind). */
  revoke: (capability: BridgeCapability) => Promise<void>;
  /** Takes down everything; for when the page goes away. */
  dispose: () => Promise<void>;
}

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

export function createAppBridgeHost(deps: AppBridgeDeps): AppBridgeHost {
  const now = deps.now ?? Date.now;
  const admit = createCallLimiter(now);
  /** What the page has set up on the desktop and not yet taken down. */
  const live = { tray: false, shortcuts: new Set<string>(), float: false };

  async function gate(capability: BridgeCapability, detail: string | null): Promise<void> {
    if (isDesktopCapability(capability) && !deps.surface) {
      throw new BridgeError('unsupported', `${capability} is only available in the app's own desktop window`);
    }
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

  /** The user may revoke while a call is in flight; what that call just set up must not outlive the decision. */
  async function settle(capability: BridgeCapability): Promise<void> {
    if (deps.getStatuses()[capability] !== 'allowed') {
      await revoke(capability);
    }
  }

  function surface(): AppSurfaceDeps {
    // `gate` has refused every desktop capability without a surface before any handler reaches this.
    if (!deps.surface) {
      throw new BridgeError('unsupported', 'No desktop window');
    }
    return deps.surface;
  }

  async function perform(
    capability: BridgeCapability,
    detail: string | null,
    action: () => Promise<boolean>,
    failure: string,
  ): Promise<null> {
    let done = false;
    try {
      done = await action();
    } catch {
      done = false;
    }
    deps.reportActivity({ capability, outcome: done ? 'ok' : 'error', detail });
    if (!done) {
      throw new BridgeError('failed', failure);
    }
    await settle(capability);
    return null;
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

  async function setTray(params: unknown): Promise<null> {
    await gate('tray', null);
    const parsed = parseTrayParams(params);
    if (!parsed) {
      throw new BridgeError('invalid_params', 'tray.set needs a title (up to 24 characters) or a tooltip (up to 80)');
    }
    return perform(
      'tray',
      null,
      async () => {
        const done = await surface().setTray(parsed.title, parsed.tooltip);
        live.tray ||= done;
        return done;
      },
      'The tray item could not be set',
    );
  }

  async function clearTray(): Promise<null> {
    await gate('tray', null);
    return perform(
      'tray',
      null,
      async () => {
        const done = await surface().clearTray();
        live.tray &&= !done;
        return done;
      },
      'The tray item could not be removed',
    );
  }

  async function registerShortcut(params: unknown): Promise<null> {
    const accelerator = parseShortcutParams(params);
    await gate('shortcut', accelerator);
    if (!accelerator) {
      throw new BridgeError('invalid_params', 'shortcut needs an accelerator such as "CommandOrControl+Shift+K"');
    }
    if (!deps.getDeclaredShortcuts().includes(accelerator)) {
      deps.reportActivity({ capability: 'shortcut', outcome: 'denied', detail: accelerator });
      throw new BridgeError('shortcut_not_granted', `${accelerator} is not among the key combinations this app declared`);
    }
    const result = await surface()
      .registerShortcut(accelerator)
      .catch((): 'failed' => 'failed');
    deps.reportActivity({ capability: 'shortcut', outcome: result === 'ok' ? 'ok' : 'error', detail: accelerator });
    if (result === 'conflict') {
      throw new BridgeError('shortcut_conflict', `${accelerator} is already taken`);
    }
    if (result !== 'ok') {
      throw new BridgeError('failed', 'The key combination could not be registered');
    }
    live.shortcuts.add(accelerator);
    await settle('shortcut');
    return null;
  }

  async function unregisterShortcut(params: unknown): Promise<null> {
    const accelerator = parseShortcutParams(params);
    await gate('shortcut', accelerator);
    if (!accelerator) {
      throw new BridgeError('invalid_params', 'shortcut needs an accelerator such as "CommandOrControl+Shift+K"');
    }
    return perform(
      'shortcut',
      accelerator,
      async () => {
        const done = await surface().unregisterShortcut(accelerator);
        if (done) {
          live.shortcuts.delete(accelerator);
        }
        return done;
      },
      'The key combination could not be released',
    );
  }

  async function setFloat(params: unknown): Promise<null> {
    const on = parseFloatParams(params);
    await gate('float', on === null ? null : on ? 'on' : 'off');
    if (on === null) {
      throw new BridgeError('invalid_params', 'float.set needs { on: true | false }');
    }
    return perform(
      'float',
      on ? 'on' : 'off',
      async () => {
        const done = await surface().setFloat(on);
        live.float = done ? on : live.float;
        return done;
      },
      'The window could not be kept on top',
    );
  }

  async function dispatch(method: string, params: unknown): Promise<unknown> {
    switch (method) {
      case 'notify':
        return notify(params);
      case 'fetch':
        return fetchThroughHost(params);
      case 'tray.set':
        return setTray(params);
      case 'tray.clear':
        return clearTray();
      case 'shortcut.register':
        return registerShortcut(params);
      case 'shortcut.unregister':
        return unregisterShortcut(params);
      case 'float.set':
        return setFloat(params);
      default:
        throw new BridgeError('method_not_found', `Unknown method: ${method.slice(0, 40)}`);
    }
  }

  async function revoke(capability: BridgeCapability): Promise<void> {
    if (!deps.surface) {
      return;
    }
    // Each step is attempted whatever the others did; a refusal from the shell must not leave the rest standing.
    if (capability === 'tray' && live.tray) {
      live.tray = false;
      await deps.surface.clearTray().catch(() => false);
    } else if (capability === 'shortcut' && live.shortcuts.size > 0) {
      live.shortcuts.clear();
      await deps.surface.unregisterShortcut(null).catch(() => false);
    } else if (capability === 'float' && live.float) {
      live.float = false;
      await deps.surface.setFloat(false).catch(() => false);
    }
  }

  return {
    handle: async (data) => {
      const request = parseRpcRequest(data);
      if (!request) {
        return null;
      }
      try {
        return rpcResult(request.id, await dispatch(request.method, request.params));
      } catch (error) {
        return rpcFailure(
          request.id,
          error instanceof BridgeError ? error : new BridgeError('failed', 'Unexpected error'),
        );
      }
    },
    revoke,
    dispose: async () => {
      await Promise.all(BRIDGE_CAPABILITIES.map(revoke));
    },
  };
}
