/**
 * A backend that sent the headers of an answer and then went quiet must not freeze the sync layer. The read of a clashing
 * key and the initial load give up once the deadline of the answer body has passed and go on through their usual retry
 * paths: the key is free again and its edit is delivered on a later send, the initial load ends offline and is retried.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { RESPONSE_BODY_DEADLINE_MS } from '@/services/config/adapters/BaseAdapter';
import { getConfigSyncManager, resetConfigSyncManager } from '@/services/config/ConfigSyncManager';
import { serveCloudBackend, storedKeys } from './fakeCloudBackend';

vi.mock('@/lib/deploy-mode', () => ({
  isLocalMode: () => false,
  getApiBaseUrl: () => 'http://test.local',
}));
vi.mock('@/lib/backend-health', () => ({
  ensureLocalBackendReady: vi.fn(),
}));
vi.mock('@/lib/platform-readiness', () => ({
  ensurePlatformReadiness: vi.fn(),
}));
vi.mock('@/lib/guest', () => ({
  getAuthToken: () => 'test-token',
}));

const answer = (body: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(body) }) as Response;

/** An answer with status 200 whose body never ends. */
const stalledAnswer = () => ({ ok: true, status: 200, json: () => new Promise<unknown>(() => undefined) }) as Response;

describe('the sync layer facing an answer body that never ends', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(console, 'warn').mockImplementation(() => {});
    resetConfigSyncManager();
    localStorage.clear();
  });

  afterEach(() => {
    resetConfigSyncManager();
    vi.clearAllTimers();
    vi.useRealTimers();
    vi.restoreAllMocks();
    localStorage.clear();
  });

  it('frees a clashing key whose server row cannot be read, and delivers its edit on a later send', async () => {
    serveCloudBackend();
    const normal = global.fetch;
    const manager = getConfigSyncManager();
    await manager.initialize();

    let sends = 0;
    let rowReadStalled = false;
    global.fetch = vi.fn((url: RequestInfo | URL, init?: RequestInit) => {
      if (init?.method === 'POST') {
        sends += 1;
        return Promise.resolve(
          sends === 1
            ? answer({ success: false, conflicts: ['chatSettings'], newVersions: {} })
            : answer({ success: true, conflicts: [], newVersions: { chatSettings: '2000_0' } }),
        );
      }
      if (!rowReadStalled && new URL(String(url)).pathname === '/config/chatSettings') {
        rowReadStalled = true;
        return Promise.resolve(stalledAnswer());
      }
      return normal(url, init);
    }) as unknown as typeof fetch;

    manager.set('chatSettings', { a: 1 } as never);
    const sent = manager.forceSync();
    await vi.advanceTimersByTimeAsync(RESPONSE_BODY_DEADLINE_MS - 1);
    expect(manager.status).toBe('syncing');

    await vi.advanceTimersByTimeAsync(1);

    expect(manager.status).toBe('offline');
    expect(storedKeys()).toEqual(['chatSettings']);
    await sent;

    await vi.advanceTimersByTimeAsync(1000);

    expect(sends).toBe(2);
    expect(manager.status).toBe('idle');
    expect(storedKeys()).toEqual([]);
  });

  it('ends the initial load offline once every attempt has waited out the deadline', async () => {
    global.fetch = vi.fn(() => Promise.resolve(stalledAnswer())) as unknown as typeof fetch;
    const manager = getConfigSyncManager();

    const settled = vi.fn();
    void manager.initialize().then(settled);
    await vi.advanceTimersByTimeAsync(RESPONSE_BODY_DEADLINE_MS);
    expect(settled).not.toHaveBeenCalled();
    expect(manager.status).toBe('loading');

    await vi.advanceTimersByTimeAsync(2 * RESPONSE_BODY_DEADLINE_MS + 5000);

    expect(settled).toHaveBeenCalledTimes(1);
    expect(manager.status).toBe('offline');
    expect(manager.isInitialized).toBe(false);
  });
});
