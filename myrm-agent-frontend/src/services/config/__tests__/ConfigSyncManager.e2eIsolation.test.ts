/**
 * While the E2E bridge freezes the search configuration (`window.__MYRM_E2E_BLOCK_SEARCH_SYNC__`), an edit of it stays on
 * the page: the cache holds it, but subscribers are not told and the backend never receives it. Every other key keeps
 * syncing, so a run cannot leak its search setup into the account yet still exercises the rest of the sync layer.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getConfigSyncManager, resetConfigSyncManager } from '@/services/config/ConfigSyncManager';
import { DEFAULT_PERSONAL_SETTINGS, DEFAULT_SEARCH_SERVICES } from '@/services/config/types';

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

const okResponse = (body: unknown) => ({ ok: true, status: 200, json: () => Promise.resolve(body) }) as Response;

/** A backend that holds no rows and accepts every write; returns the keys of each write it received. */
function serveBackend(): string[][] {
  const writes: string[][] = [];
  global.fetch = vi.fn((_url: RequestInfo | URL, init?: RequestInit) => {
    if (init?.method !== 'POST') {
      return Promise.resolve(okResponse({ configs: {} }));
    }
    const { changes } = JSON.parse(String(init.body)) as { changes: Array<{ key: string }> };
    writes.push(changes.map((change) => change.key));
    return Promise.resolve(okResponse({ success: true, conflicts: [], newVersions: {} }));
  }) as unknown as typeof fetch;
  return writes;
}

describe('ConfigSyncManager while the E2E bridge freezes the search configuration', () => {
  beforeEach(() => {
    resetConfigSyncManager();
    localStorage.clear();
    window.__MYRM_E2E_BLOCK_SEARCH_SYNC__ = true;
  });

  afterEach(() => {
    delete window.__MYRM_E2E_BLOCK_SEARCH_SYNC__;
    resetConfigSyncManager();
    localStorage.clear();
  });

  it('keeps an edit of the search services on the page: cached, but neither announced nor sent', async () => {
    const writes = serveBackend();
    const manager = getConfigSyncManager();
    await manager.initialize();
    const listener = vi.fn();
    manager.subscribe('searchServices', listener);
    expect(manager.get('searchServices')).toBeNull();

    manager.set('searchServices', DEFAULT_SEARCH_SERVICES);
    await manager.forceSync();

    expect(manager.get('searchServices')).toEqual(DEFAULT_SEARCH_SERVICES);
    expect(listener).not.toHaveBeenCalled();
    expect(writes).toEqual([]);
  });

  it('still announces and sends an edit of any other key', async () => {
    const writes = serveBackend();
    const manager = getConfigSyncManager();
    await manager.initialize();
    const listener = vi.fn();
    manager.subscribe('personalSettings', listener);
    const edited = { ...DEFAULT_PERSONAL_SETTINGS, systemInstructions: 'edited' };

    manager.set('personalSettings', edited);
    await manager.forceSync();

    expect(listener).toHaveBeenCalledOnce();
    expect(writes).toEqual([['personalSettings']]);
  });
});
