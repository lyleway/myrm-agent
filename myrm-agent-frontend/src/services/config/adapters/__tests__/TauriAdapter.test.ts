import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { markLocalBackendUnreachable } from '@/lib/backend-health';
import { whenDatabaseReady } from '@/lib/platform-readiness';
import { RESPONSE_BODY_DEADLINE_MS } from '@/services/config/adapters/BaseAdapter';
import { TauriConfigAdapter } from '@/services/config/adapters/TauriAdapter';
import { ConfigSyncError, DEFAULT_PERSONAL_SETTINGS, type ConfigChange } from '@/services/config/types';
import {
  answerWithBody,
  brokenBody,
  malformedBody,
  runOutBodyDeadline,
  signalOfRequest,
  stalledBody,
} from './bodyDeadlineKit';

vi.mock('@/lib/deploy-mode', () => ({
  getApiBaseUrl: () => '/api/v1',
}));

vi.mock('@/lib/backend-health', () => ({
  markLocalBackendUnreachable: vi.fn(),
}));

vi.mock('@/lib/platform-readiness', () => ({
  whenDatabaseReady: vi.fn(async () => true),
}));

const CHANGES: ConfigChange[] = [
  {
    key: 'personalSettings',
    value: { ...DEFAULT_PERSONAL_SETTINGS, systemInstructions: 'test' },
    expectedVersion: '0_0',
    timestamp: Date.now(),
  },
];

const BACKEND_NOT_AVAILABLE = {
  success: false,
  conflicts: [],
  newVersions: new Map(),
  error: 'Backend not available',
};

/** A row as the backend answers it, and the record the adapter hands the sync layer for it. */
const API_ROW = {
  key: 'chatSettings',
  value: { sample: true },
  version: '2000_1',
  updatedAt: '2026-11-26T00:00:00Z',
  deviceId: 'other-device',
  isSystemDefault: true,
};

const API_ROW_RECORD = {
  key: 'chatSettings',
  value: { sample: true },
  meta: { version: '2000_1', updatedAt: '2026-11-26T00:00:00Z', deviceId: 'other-device' },
  isSystemDefault: true,
};

/** The `fetch` double of an answer whose JSON body is `body`. */
const answer = (status: number, body: unknown = {}) => answerWithBody(status, async () => body);

describe('TauriConfigAdapter', () => {
  const originalFetch = globalThis.fetch;
  let adapter: TauriConfigAdapter;

  beforeEach(() => {
    adapter = new TauriConfigAdapter();
    vi.mocked(markLocalBackendUnreachable).mockClear();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.stubGlobal('fetch', originalFetch);
    vi.restoreAllMocks();
  });

  it('sync treats Next proxy HTTP 500 like Failed to fetch', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 500,
      statusText: 'Internal Server Error',
    } as Response);

    await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
    expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
  });

  it.each(['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.'])(
    'sync reports Backend not available when the browser fails the request (%s)',
    async (message) => {
      vi.mocked(fetch).mockRejectedValueOnce(new TypeError(message));

      await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
    },
  );

  it('sync does not mistake a malformed answer for a network failure', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ success: true }),
    } as Response);

    await expect(adapter.sync(CHANGES)).rejects.toBeInstanceOf(TypeError);
    expect(markLocalBackendUnreachable).not.toHaveBeenCalled();
  });

  it('sync lets an exception that is not a failed request through', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new Error('programming error'));

    await expect(adapter.sync(CHANGES)).rejects.toThrow('programming error');
    expect(markLocalBackendUnreachable).not.toHaveBeenCalled();
  });

  it('getAll throws on HTTP 502 to enable ConfigSyncManager retry', async () => {
    vi.mocked(fetch).mockResolvedValueOnce({
      ok: false,
      status: 502,
      statusText: 'Bad Gateway',
    } as Response);

    await expect(adapter.getAll()).rejects.toThrow('Backend not available');
  });

  it('getAll throws when localFetch times out (AbortError) to enable retry', async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new DOMException('The operation was aborted.', 'AbortError'));

    await expect(adapter.getAll()).rejects.toThrow('Backend not available');
    expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
  });

  describe('get', () => {
    it('answers null only when the backend says the row does not exist', async () => {
      vi.mocked(fetch).mockResolvedValueOnce({ ok: false, status: 404, statusText: 'Not Found' } as Response);

      await expect(adapter.get('chatSettings')).resolves.toBeNull();
    });

    it('returns the row with its version and writer, asked for with the session cookie', async () => {
      vi.mocked(fetch).mockImplementationOnce(answer(200, API_ROW));

      await expect(adapter.get('chatSettings')).resolves.toEqual(API_ROW_RECORD);
      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/config/chatSettings',
        expect.objectContaining({ credentials: 'include' }),
      );
    });

    it('hands on a row the backend could not decrypt as unreadable instead of dropping it', async () => {
      vi.mocked(fetch).mockImplementationOnce(answer(200, { ...API_ROW, unreadable: true }));

      await expect(adapter.get('chatSettings')).resolves.toMatchObject({ unreadable: true });
    });

    it.each([401, 403, 422])(
      'throws the refusal itself on HTTP %i, without calling the backend unreachable',
      async (status) => {
        vi.mocked(fetch).mockImplementationOnce(answer(status));

        await expect(adapter.get('chatSettings')).rejects.toThrow(
          new ConfigSyncError(`Failed to get config 'chatSettings': HTTP ${status}`),
        );
        expect(markLocalBackendUnreachable).not.toHaveBeenCalled();
      },
    );

    it.each([500, 502, 503, 504])('throws on HTTP %i: the state of the row is unknown', async (status) => {
      vi.mocked(fetch).mockResolvedValueOnce({ ok: false, status, statusText: 'Unavailable' } as Response);

      await expect(adapter.get('chatSettings')).rejects.toThrow(new ConfigSyncError('Backend not available'));
      expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
    });

    it.each([new TypeError('Load failed'), new DOMException('The operation was aborted.', 'AbortError')])(
      'throws when the request never gets an answer (%s)',
      async (failure) => {
        vi.mocked(fetch).mockRejectedValueOnce(failure);

        await expect(adapter.get('chatSettings')).rejects.toThrow(new ConfigSyncError('Backend not available'));
        expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
      },
    );
  });

  describe('getAll', () => {
    it('asks only for the given keys, carrying the session cookie', async () => {
      vi.mocked(fetch).mockImplementationOnce(answer(200, { configs: {} }));

      await adapter.getAll(['chatSettings', 'personalSettings']);

      expect(fetch).toHaveBeenCalledWith(
        '/api/v1/config?keys=chatSettings%2CpersonalSettings',
        expect.objectContaining({ credentials: 'include' }),
      );
    });

    it('returns the rows by key', async () => {
      vi.mocked(fetch).mockImplementationOnce(answer(200, { configs: { chatSettings: API_ROW } }));

      await expect(adapter.getAll()).resolves.toEqual(new Map([['chatSettings', API_ROW_RECORD]]));
    });

    it('answers an empty set when the backend has no rows (HTTP 404)', async () => {
      vi.mocked(fetch).mockImplementationOnce(answer(404));

      await expect(adapter.getAll()).resolves.toEqual(new Map());
    });

    it.each([401, 403, 422])(
      'throws the refusal itself on HTTP %i, without calling the backend unreachable',
      async (status) => {
        vi.mocked(fetch).mockImplementationOnce(answer(status));

        await expect(adapter.getAll()).rejects.toThrow(
          new ConfigSyncError(`Failed to get all configs: HTTP ${status}`),
        );
        expect(markLocalBackendUnreachable).not.toHaveBeenCalled();
      },
    );
  });

  describe('sync', () => {
    it('sends nothing for an empty change list', async () => {
      await expect(adapter.sync([])).resolves.toEqual({ success: true, conflicts: [], newVersions: new Map() });
      expect(fetch).not.toHaveBeenCalled();
    });

    it('passes the answer of a delivered write through, conflicts included', async () => {
      vi.mocked(fetch).mockImplementationOnce(
        answer(200, { success: false, conflicts: ['personalSettings'], newVersions: { chatSettings: '2000_1' } }),
      );

      await expect(adapter.sync(CHANGES)).resolves.toEqual({
        success: false,
        conflicts: ['personalSettings'],
        newVersions: new Map([['chatSettings', '2000_1']]),
        error: undefined,
      });
    });

    it.each([400, 422])('rejects on HTTP %i: the backend refused the write itself', async (status) => {
      vi.mocked(fetch).mockImplementationOnce(answer(status));

      await expect(adapter.sync(CHANGES)).rejects.toThrow(
        new ConfigSyncError(`Failed to sync configs: HTTP ${status}`),
      );
      expect(markLocalBackendUnreachable).not.toHaveBeenCalled();
    });
  });

  describe('while the local database is not ready', () => {
    beforeEach(() => {
      vi.mocked(whenDatabaseReady).mockResolvedValueOnce(false);
    });

    it('getAll throws Backend not available without sending anything', async () => {
      await expect(adapter.getAll()).rejects.toThrow(new ConfigSyncError('Backend not available'));
      expect(fetch).not.toHaveBeenCalled();
    });

    it('get throws Backend not available without sending anything', async () => {
      await expect(adapter.get('chatSettings')).rejects.toThrow(new ConfigSyncError('Backend not available'));
      expect(fetch).not.toHaveBeenCalled();
    });

    it('sync reports Backend not available without sending anything', async () => {
      await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      expect(fetch).not.toHaveBeenCalled();
    });
  });

  describe('an answer whose body does not arrive', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('makes get throw Backend not available once the deadline has passed', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, stalledBody));

      const call = adapter.get('chatSettings');
      await runOutBodyDeadline(call);

      await expect(call).rejects.toThrow(new ConfigSyncError('Backend not available'));
      expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
      expect(signalOfRequest().aborted).toBe(true);
    });

    it('makes getAll throw Backend not available once the deadline has passed', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, stalledBody));

      const call = adapter.getAll();
      await runOutBodyDeadline(call);

      await expect(call).rejects.toThrow(new ConfigSyncError('Backend not available'));
      expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
      expect(signalOfRequest().aborted).toBe(true);
    });

    it('cancels the request of an answer it never reads once the deadline has passed', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(503, stalledBody));

      await expect(adapter.get('chatSettings')).rejects.toThrow(new ConfigSyncError('Backend not available'));
      await vi.advanceTimersByTimeAsync(RESPONSE_BODY_DEADLINE_MS - 1);
      expect(signalOfRequest().aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);

      expect(signalOfRequest().aborted).toBe(true);
    });

    it('reports Backend not available for a sync answer that never finishes', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, stalledBody));

      const call = adapter.sync(CHANGES);
      await runOutBodyDeadline(call);

      await expect(call).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
      expect(signalOfRequest().aborted).toBe(true);
    });

    it('reports Backend not available when the connection breaks while a sync answer is read', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, brokenBody));

      await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      expect(markLocalBackendUnreachable).toHaveBeenCalledTimes(1);
    });

    it('lets an answer that is not JSON through: that is a defect, not an unreachable backend', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, malformedBody));

      await expect(adapter.sync(CHANGES)).rejects.toBeInstanceOf(SyntaxError);
      expect(markLocalBackendUnreachable).not.toHaveBeenCalled();
    });
  });
});
