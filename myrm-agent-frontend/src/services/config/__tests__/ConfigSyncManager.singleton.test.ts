/**
 * The page-wide manager: one instance until it is reset. An instance without `setConflictResolver`, which the app shell
 * calls at start-up, cannot serve the app and is replaced instead of handed out.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { getConfigSyncManager, resetConfigSyncManager } from '@/services/config/ConfigSyncManager';

vi.mock('@/lib/deploy-mode', () => ({
  isLocalMode: () => false,
  getApiBaseUrl: () => 'http://test.local',
}));
vi.mock('@/lib/guest', () => ({
  getAuthToken: () => 'test-token',
}));

describe('getConfigSyncManager', () => {
  beforeEach(() => {
    resetConfigSyncManager();
  });

  afterEach(() => {
    resetConfigSyncManager();
  });

  it('hands out one manager until it is reset', () => {
    const first = getConfigSyncManager();

    expect(getConfigSyncManager()).toBe(first);

    resetConfigSyncManager();
    expect(getConfigSyncManager()).not.toBe(first);
  });

  it('replaces an instance that lacks setConflictResolver', () => {
    const stale = getConfigSyncManager();
    Object.defineProperty(stale, 'setConflictResolver', { value: undefined });

    const fresh = getConfigSyncManager();

    expect(fresh).not.toBe(stale);
    expect(fresh.setConflictResolver).toBeTypeOf('function');
    expect(getConfigSyncManager()).toBe(fresh);
  });
});
