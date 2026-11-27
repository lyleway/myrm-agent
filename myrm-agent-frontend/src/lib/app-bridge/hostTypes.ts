/**
 * [INPUT]
 * - ./protocol (POS: capability vocabulary, parsed params, RpcResponse)
 * - ./declaration::CapabilityStatus (POS: whether a declared capability may be used)
 *
 * [OUTPUT]
 * - AppBridgeDeps / AppSurfaceDeps: what the host is given by its caller (status reads, proxies, the desktop surface).
 * - AppBridgeActivity / ProxyFetchRequest / ProxyFetchReply / ProxyAiRequest / ProxyAiReply: what the host reports and sends on.
 * - AppBridgeHost: what the host hands back (`handle`, `revoke`, `dispose`).
 *
 * [POS]
 * Types only: the seam between the bridge host and the code that embeds it (`useAppBridge`, the service layer and the
 * desktop shell). Kept apart from `host.ts` so the dependency direction is visible in one place.
 */

import type { CapabilityStatus } from './declaration';
import type { AiParams, BridgeCapability, FetchParams, RpcResponse } from './protocol';

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

export interface ProxyAiRequest extends AiParams {
  version_sha256: string;
}

export interface ProxyAiReply {
  ok: boolean;
  text: string;
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
  proxyAi: (request: ProxyAiRequest) => Promise<ProxyAiReply>;
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
