import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { getAuthToken } from '@/lib/guest';
import { RESPONSE_BODY_DEADLINE_MS } from '@/services/config/adapters/BaseAdapter';
import { SandboxConfigAdapter } from '@/services/config/adapters/SandboxAdapter';
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
  getApiBaseUrl: () => 'http://test.local',
}));

vi.mock('@/lib/guest', () => ({
  getAuthToken: vi.fn(() => 'test-token'),
}));

const reply = (status: number, body: unknown = {}) =>
  ({ ok: status >= 200 && status < 300, status, statusText: `HTTP ${status}`, json: async () => body }) as Response;

const CHANGES: ConfigChange[] = [
  {
    key: 'personalSettings',
    value: { ...DEFAULT_PERSONAL_SETTINGS, systemInstructions: 'edited' },
    expectedVersion: '1000_0',
    timestamp: 1,
  },
];

const BACKEND_NOT_AVAILABLE = {
  success: false,
  conflicts: [],
  newVersions: new Map(),
  error: 'Backend not available',
};

/** A row as the API answers it, and the record the adapter hands the sync layer for it. */
const API_ROW = {
  key: 'chatSettings',
  value: { sample: true },
  version: '2000_1',
  updatedAt: '2026-11-26T00:00:00Z',
  deviceId: 'other-device',
  encrypted: false,
};

const API_ROW_RECORD = {
  key: 'chatSettings',
  value: { sample: true },
  meta: { version: '2000_1', updatedAt: '2026-11-26T00:00:00Z', deviceId: 'other-device' },
  encrypted: false,
};

describe('SandboxConfigAdapter', () => {
  const originalFetch = globalThis.fetch;
  let adapter: SandboxConfigAdapter;

  beforeEach(() => {
    adapter = new SandboxConfigAdapter();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.stubGlobal('fetch', originalFetch);
    vi.mocked(getAuthToken).mockReturnValue('test-token');
  });

  describe('get', () => {
    it('answers null only when the server says the row does not exist', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(404));

      await expect(adapter.get('chatSettings')).resolves.toBeNull();
    });

    it('returns the row with its version, writer and encryption flag, asked for with the session token', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(200, API_ROW));

      await expect(adapter.get('chatSettings')).resolves.toEqual(API_ROW_RECORD);
      expect(fetch).toHaveBeenCalledWith(
        'http://test.local/config/chatSettings',
        expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer test-token' }) }),
      );
    });

    it('hands on a row the server could not decrypt as unreadable instead of dropping it', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(200, { ...API_ROW, encrypted: true, unreadable: true }));

      await expect(adapter.get('chatSettings')).resolves.toMatchObject({ encrypted: true, unreadable: true });
    });

    it.each([401, 500, 502, 503])('throws on HTTP %i: the state of the row is unknown', async (status) => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(status));

      await expect(adapter.get('chatSettings')).rejects.toThrow(ConfigSyncError);
    });

    it('throws when the session has no token', async () => {
      vi.mocked(getAuthToken).mockReturnValue(null);

      await expect(adapter.get('chatSettings')).rejects.toThrow(ConfigSyncError);
      expect(fetch).not.toHaveBeenCalled();
    });

    it('throws when the request never gets an answer', async () => {
      vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Load failed'));

      await expect(adapter.get('chatSettings')).rejects.toThrow('Load failed');
    });
  });

  describe('getAll', () => {
    it('asks for every row when no keys are given and returns the rows by key', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(200, { configs: { chatSettings: API_ROW } }));

      await expect(adapter.getAll()).resolves.toEqual(new Map([['chatSettings', API_ROW_RECORD]]));
      expect(fetch).toHaveBeenCalledWith('http://test.local/config', expect.anything());
    });

    it('asks only for the given keys', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(200, { configs: {} }));

      await adapter.getAll(['chatSettings', 'personalSettings']);

      expect(fetch).toHaveBeenCalledWith(
        'http://test.local/config?keys=chatSettings%2CpersonalSettings',
        expect.anything(),
      );
    });

    it('answers an empty set when the server does not know the session (HTTP 401)', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(401));

      await expect(adapter.getAll()).resolves.toEqual(new Map());
    });

    it('answers an empty set when the session has no token, without sending anything', async () => {
      vi.mocked(getAuthToken).mockReturnValue(null);

      await expect(adapter.getAll()).resolves.toEqual(new Map());
      expect(fetch).not.toHaveBeenCalled();
    });

    it.each([403, 500, 503])('throws on HTTP %i: the rows are unknown', async (status) => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(status));

      await expect(adapter.getAll()).rejects.toThrow(new ConfigSyncError(`Failed to get all configs: HTTP ${status}`));
    });
  });

  describe('sync', () => {
    it('sends nothing for an empty change list', async () => {
      await expect(adapter.sync([])).resolves.toEqual({ success: true, conflicts: [], newVersions: new Map() });
      expect(fetch).not.toHaveBeenCalled();
    });

    it('posts the changes with the device id and the session token', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(200, { success: true, conflicts: [], newVersions: {} }));

      await adapter.sync(CHANGES);

      expect(fetch).toHaveBeenCalledWith(
        'http://test.local/config/sync',
        expect.objectContaining({
          method: 'POST',
          headers: expect.objectContaining({ Authorization: 'Bearer test-token', 'Content-Type': 'application/json' }),
          body: JSON.stringify({ changes: CHANGES, deviceId: adapter.getDeviceId() }),
        }),
      );
    });

    it.each(['Failed to fetch', 'Load failed', 'NetworkError when attempting to fetch resource.'])(
      'reports Backend not available when the browser fails the request (%s)',
      async (message) => {
        vi.mocked(fetch).mockRejectedValueOnce(new TypeError(message));

        await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      },
    );

    it('reports Backend not available when the request times out', async () => {
      vi.mocked(fetch).mockRejectedValueOnce(new DOMException('The operation was aborted.', 'AbortError'));

      await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
    });

    it.each([401, 408, 429, 502, 503, 504])(
      'reports Backend not available on HTTP %i: the same write may succeed later',
      async (status) => {
        vi.mocked(fetch).mockResolvedValueOnce(reply(status));

        await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      },
    );

    it('reports Backend not available when the session has no token, without sending anything', async () => {
      vi.mocked(getAuthToken).mockReturnValue(null);

      await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      expect(fetch).not.toHaveBeenCalled();
    });

    it.each([400, 422, 500])('rejects on HTTP %i: the server refused the write itself', async (status) => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(status));

      await expect(adapter.sync(CHANGES)).rejects.toThrow('Failed to sync configs');
    });

    it('does not mistake a malformed answer for a network failure', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(reply(200, { success: true }));

      await expect(adapter.sync(CHANGES)).rejects.toBeInstanceOf(TypeError);
    });

    it('lets an exception that is not a failed request through', async () => {
      vi.mocked(fetch).mockRejectedValueOnce(new Error('programming error'));

      await expect(adapter.sync(CHANGES)).rejects.toThrow('programming error');
    });

    it('passes the answer of a delivered write through, conflicts included', async () => {
      vi.mocked(fetch).mockResolvedValueOnce(
        reply(200, { success: false, conflicts: ['personalSettings'], newVersions: { chatSettings: '2000_1' } }),
      );

      await expect(adapter.sync(CHANGES)).resolves.toEqual({
        success: false,
        conflicts: ['personalSettings'],
        newVersions: new Map([['chatSettings', '2000_1']]),
        error: undefined,
      });
    });
  });

  describe('an answer whose body does not arrive', () => {
    beforeEach(() => {
      vi.useFakeTimers();
    });

    afterEach(() => {
      vi.useRealTimers();
    });

    it('makes get throw once the deadline has passed: the state of the row is unknown', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, stalledBody));

      const call = adapter.get('chatSettings');
      await runOutBodyDeadline(call);

      await expect(call).rejects.toHaveProperty('name', 'AbortError');
      expect(signalOfRequest().aborted).toBe(true);
    });

    it('makes getAll throw once the deadline has passed', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, stalledBody));

      const call = adapter.getAll(['chatSettings']);
      await runOutBodyDeadline(call);

      await expect(call).rejects.toHaveProperty('name', 'AbortError');
      expect(signalOfRequest().aborted).toBe(true);
    });

    it('cancels the request of an answer it never reads once the deadline has passed', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(503, stalledBody));

      await expect(adapter.get('chatSettings')).rejects.toThrow(ConfigSyncError);
      await vi.advanceTimersByTimeAsync(RESPONSE_BODY_DEADLINE_MS - 1);
      expect(signalOfRequest().aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);

      expect(signalOfRequest().aborted).toBe(true);
    });

    it('makes get throw when the connection breaks while the body is read', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, brokenBody));

      await expect(adapter.get('chatSettings')).rejects.toBeInstanceOf(TypeError);
    });

    it('reports Backend not available for a sync answer that never finishes', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, stalledBody));

      const call = adapter.sync(CHANGES);
      await runOutBodyDeadline(call);

      await expect(call).resolves.toEqual(BACKEND_NOT_AVAILABLE);
      expect(signalOfRequest().aborted).toBe(true);
    });

    it('reports Backend not available when the connection breaks while a sync answer is read', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, brokenBody));

      await expect(adapter.sync(CHANGES)).resolves.toEqual(BACKEND_NOT_AVAILABLE);
    });

    it('lets a sync answer that is not JSON through: that is a server defect, not an unreachable backend', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, malformedBody));

      await expect(adapter.sync(CHANGES)).rejects.toBeInstanceOf(SyntaxError);
    });
  });
});
