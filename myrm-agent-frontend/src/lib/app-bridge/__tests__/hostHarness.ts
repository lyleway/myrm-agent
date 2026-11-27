import { vi } from 'vitest';
import type { CapabilityStatus } from '../declaration';
import { createAppBridgeHost } from '../host';
import type {
  AppBridgeActivity,
  AppSurfaceDeps,
  ProxyAiReply,
  ProxyAiRequest,
  ProxyFetchReply,
  ProxyFetchRequest,
} from '../hostTypes';
import type { BridgeCapability, RpcResponse } from '../protocol';

export const SHA = 'c'.repeat(64);

export const DECLARED_SHORTCUTS = ['CommandOrControl+Shift+K', 'Alt+F1'] as const;

export function createSurface() {
  return {
    setTray: vi.fn<AppSurfaceDeps['setTray']>().mockResolvedValue(true),
    clearTray: vi.fn<AppSurfaceDeps['clearTray']>().mockResolvedValue(true),
    registerShortcut: vi.fn<AppSurfaceDeps['registerShortcut']>().mockResolvedValue('ok'),
    unregisterShortcut: vi.fn<AppSurfaceDeps['unregisterShortcut']>().mockResolvedValue(true),
    setFloat: vi.fn<AppSurfaceDeps['setFloat']>().mockResolvedValue(true),
  };
}

/** Every call the shell's side received, whichever function it was. */
export const surfaceCalls = (surface: ReturnType<typeof createSurface>): unknown[][] =>
  Object.values(surface).flatMap((fn) => fn.mock.calls as unknown[][]);

interface SetupOptions {
  /** `false` is a page outside the app's own desktop window. */
  desktop?: boolean;
}

/** Host under test with every capability allowed unless a status says otherwise; the statuses can change mid-test. */
export function setup(statuses: Partial<Record<BridgeCapability, CapabilityStatus>> = {}, options: SetupOptions = {}) {
  const clock = { at: 1_000_000 };
  const current: Record<BridgeCapability, CapabilityStatus> = {
    notify: 'allowed',
    fetch: 'allowed',
    ai: 'allowed',
    tray: 'allowed',
    shortcut: 'allowed',
    float: 'allowed',
    ...statuses,
  };
  const activity: AppBridgeActivity[] = [];
  const proxyFetch = vi.fn<(request: ProxyFetchRequest) => Promise<ProxyFetchReply>>();
  const proxyAi = vi.fn<(request: ProxyAiRequest) => Promise<ProxyAiReply>>();
  const showNotification = vi.fn<(title: string, body: string) => Promise<void>>().mockResolvedValue(undefined);
  const surface = createSurface();
  const host = createAppBridgeHost({
    appName: 'Weather',
    versionSha256: SHA,
    getStatuses: () => current,
    getDeclaredShortcuts: () => DECLARED_SHORTCUTS,
    proxyFetch,
    proxyAi,
    surface: options.desktop === false ? null : surface,
    reportActivity: (entry) => activity.push(entry),
    showNotification,
    now: () => clock.at,
  });
  return { handle: host.handle, host, clock, current, activity, proxyFetch, proxyAi, showNotification, surface };
}

export const request = (method: string, params?: unknown, id: number | string = 1) => ({
  jsonrpc: '2.0',
  id,
  method,
  params,
});

export const reasonOf = (reply: RpcResponse | null) =>
  reply && 'error' in reply ? reply.error.data.reason : undefined;

export const okReply = (body = '{"t":1}'): ProxyFetchReply => ({
  ok: true,
  status: 200,
  headers: { 'content-type': 'application/json' },
  body,
  code: null,
  message: null,
});

export const aiReply = (text = 'Sunny'): ProxyAiReply => ({ ok: true, text, code: null, message: null });
