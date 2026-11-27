/**
 * Start-up in local (desktop) mode while the local backend is not answering: the manager stays offline and starts again
 * by itself only when the platform reports its database ready and nothing else has started it in the meantime.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ensureLocalBackendReady } from '@/lib/backend-health';
import { ensurePlatformReadiness, type PlatformReadinessSnapshot } from '@/lib/platform-readiness';
import { getConfigSyncManager, resetConfigSyncManager } from '@/services/config/ConfigSyncManager';

vi.mock('@/lib/deploy-mode', () => ({
  isLocalMode: () => true,
  getApiBaseUrl: () => 'http://local.test/api/v1',
}));
vi.mock('@/lib/backend-health', () => ({
  ensureLocalBackendReady: vi.fn(),
  markLocalBackendUnreachable: vi.fn(),
}));
vi.mock('@/lib/platform-readiness', () => ({
  ensurePlatformReadiness: vi.fn(),
  whenDatabaseReady: vi.fn(() => Promise.resolve(true)),
}));

const emptyConfigs = () =>
  Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve({ configs: {} }) } as Response);

/** Holds the platform's readiness answer back until the returned function lets it arrive. */
function platformAnswersLater(): (database: boolean) => void {
  let arrive!: (snapshot: PlatformReadinessSnapshot) => void;
  vi.mocked(ensurePlatformReadiness).mockReturnValue(
    new Promise<PlatformReadinessSnapshot>((resolve) => {
      arrive = resolve;
    }),
  );
  return (database) => arrive({ state: database ? 'ready' : 'warming', database });
}

/** One macrotask: every promise reaction already queued has run. */
const settle = () => new Promise<void>((resolve) => setTimeout(resolve, 0));

describe('ConfigSyncManager start-up in local mode', () => {
  let fetchMock: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    resetConfigSyncManager();
    localStorage.clear();
    vi.mocked(ensureLocalBackendReady).mockReset().mockResolvedValueOnce(false).mockResolvedValue(true);
    fetchMock = vi.fn(emptyConfigs);
    global.fetch = fetchMock as unknown as typeof fetch;
  });

  afterEach(() => {
    resetConfigSyncManager();
    localStorage.clear();
  });

  it('stays offline while the backend is down, then starts by itself once the platform reports its database ready', async () => {
    const platformAnswers = platformAnswersLater();
    const manager = getConfigSyncManager();

    await manager.initialize();
    expect(manager.status).toBe('offline');
    expect(manager.isInitialized).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();

    platformAnswers(true);

    await vi.waitFor(() => expect(manager.isInitialized).toBe(true));
    expect(manager.status).toBe('idle');
    expect(ensureLocalBackendReady).toHaveBeenCalledTimes(2);
  });

  it('does not start again while the platform reports its database not ready', async () => {
    const platformAnswers = platformAnswersLater();
    const manager = getConfigSyncManager();
    await manager.initialize();

    platformAnswers(false);
    await settle();

    expect(manager.isInitialized).toBe(false);
    expect(ensureLocalBackendReady).toHaveBeenCalledTimes(1);
  });

  it('does not start a third time when the manager already started once the backend came up', async () => {
    const platformAnswers = platformAnswersLater();
    const manager = getConfigSyncManager();
    await manager.initialize();
    await manager.initialize();
    expect(manager.isInitialized).toBe(true);
    const readsBefore = fetchMock.mock.calls.length;

    platformAnswers(true);
    await settle();

    expect(ensureLocalBackendReady).toHaveBeenCalledTimes(2);
    expect(fetchMock).toHaveBeenCalledTimes(readsBefore);
  });
});
