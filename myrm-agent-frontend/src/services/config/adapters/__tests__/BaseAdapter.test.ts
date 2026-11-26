import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  BACKEND_NOT_AVAILABLE_ERROR,
  BaseConfigAdapter,
  RESPONSE_BODY_DEADLINE_MS,
  RESPONSE_HEADERS_DEADLINE_MS,
  backendUnavailableSyncResult,
  fetchWithDeadlines,
  isLostAnswer,
  isTransportFailure,
} from '@/services/config/adapters/BaseAdapter';
import type { ConfigKey, ConfigRecord, SyncResult } from '@/services/config/types';
import { answerWithBody, brokenBody, runOutBodyDeadline, signalOfRequest, stalledBody } from './bodyDeadlineKit';

const URL_UNDER_TEST = 'http://test.local/config';

/** A request whose headers never arrive; a browser rejects it with an `AbortError` once its signal aborts. */
const silentRequest = (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
  new Promise<Response>((_resolve, reject) => {
    init?.signal?.addEventListener(
      'abort',
      () => reject(new DOMException('The operation was aborted.', 'AbortError')),
      { once: true },
    );
  });

describe('fetchWithDeadlines', () => {
  const originalFetch = globalThis.fetch;

  beforeEach(() => {
    vi.useFakeTimers();
    vi.stubGlobal('fetch', vi.fn());
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.stubGlobal('fetch', originalFetch);
  });

  describe('until the headers arrive', () => {
    it('aborts a request whose headers take longer than the headers deadline, and leaves no timer behind', async () => {
      vi.mocked(fetch).mockImplementationOnce(silentRequest);

      const answer = fetchWithDeadlines(URL_UNDER_TEST, {});
      const settled = vi.fn();
      answer.then(settled, settled);

      await vi.advanceTimersByTimeAsync(RESPONSE_HEADERS_DEADLINE_MS - 1);
      expect(settled).not.toHaveBeenCalled();
      await vi.advanceTimersByTimeAsync(1);

      await expect(answer).rejects.toHaveProperty('name', 'AbortError');
      expect(vi.getTimerCount()).toBe(0);
    });

    it('leaves no timer behind when the request fails', async () => {
      vi.mocked(fetch).mockRejectedValueOnce(new TypeError('Failed to fetch'));

      await expect(fetchWithDeadlines(URL_UNDER_TEST, {})).rejects.toBeInstanceOf(TypeError);
      expect(vi.getTimerCount()).toBe(0);
    });

    it('hands the request over as given, with its own abort signal', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, async () => ({})));

      await fetchWithDeadlines(URL_UNDER_TEST, { method: 'POST', body: '{}' });

      expect(fetch).toHaveBeenCalledWith(URL_UNDER_TEST, {
        method: 'POST',
        body: '{}',
        signal: expect.any(AbortSignal),
      });
    });
  });

  describe('once the headers have arrived', () => {
    it('does not hold the body to the headers deadline', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, async () => ({})));
      await fetchWithDeadlines(URL_UNDER_TEST, {});

      await vi.advanceTimersByTimeAsync(RESPONSE_HEADERS_DEADLINE_MS + 1);

      expect(signalOfRequest().aborted).toBe(false);
    });

    it('cancels the request when nobody reads the body within the body deadline', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(503, stalledBody));
      await fetchWithDeadlines(URL_UNDER_TEST, {});

      await vi.advanceTimersByTimeAsync(RESPONSE_BODY_DEADLINE_MS - 1);
      expect(signalOfRequest().aborted).toBe(false);
      await vi.advanceTimersByTimeAsync(1);

      expect(signalOfRequest().aborted).toBe(true);
    });

    it('cancels the request and rejects the read of a body that stalls', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, stalledBody));
      const { readJson } = await fetchWithDeadlines(URL_UNDER_TEST, {});

      const read = readJson();
      await runOutBodyDeadline(read);

      await expect(read).rejects.toHaveProperty('name', 'AbortError');
      expect(signalOfRequest().aborted).toBe(true);
    });

    it('leaves no timer behind once the body is read, and never aborts afterwards', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, async () => ({ rows: 2 })));
      const { readJson } = await fetchWithDeadlines(URL_UNDER_TEST, {});

      await expect(readJson()).resolves.toEqual({ rows: 2 });
      expect(vi.getTimerCount()).toBe(0);

      await vi.advanceTimersByTimeAsync(2 * RESPONSE_BODY_DEADLINE_MS);
      expect(signalOfRequest().aborted).toBe(false);
    });

    it('leaves no timer behind when reading the body fails', async () => {
      vi.mocked(fetch).mockImplementationOnce(answerWithBody(200, brokenBody));
      const { readJson } = await fetchWithDeadlines(URL_UNDER_TEST, {});

      await expect(readJson()).rejects.toBeInstanceOf(TypeError);
      expect(vi.getTimerCount()).toBe(0);
    });
  });
});

describe('isTransportFailure', () => {
  it.each([
    ['a TypeError, which every browser throws for a failed request', new TypeError('Load failed')],
    ['the abort of the headers deadline', new DOMException('The operation was aborted.', 'AbortError')],
  ])('counts %s as a delivery failure', (_what, error) => {
    expect(isTransportFailure(error)).toBe(true);
  });

  it.each([
    ['an Error', new Error('programming error')],
    ['a DOMException that is not an abort', new DOMException('denied', 'NotAllowedError')],
    ['a SyntaxError', new SyntaxError('Unexpected token < in JSON')],
    ['a value that is not an error', 'Failed to fetch'],
  ])('does not count %s: that is a bug and stays visible', (_what, error) => {
    expect(isTransportFailure(error)).toBe(false);
  });
});

describe('isLostAnswer', () => {
  it.each([
    ['a stalled body, aborted at its deadline', new DOMException('The operation was aborted.', 'AbortError')],
    ['a connection that broke while the body was read', new TypeError('network error')],
  ])('counts %s as an answer that never made it', (_what, error) => {
    expect(isLostAnswer(error)).toBe(true);
  });

  it('does not count a body that arrived but is not JSON: that is a server defect', () => {
    expect(isLostAnswer(new SyntaxError('Unexpected token < in JSON'))).toBe(false);
  });
});

describe('backendUnavailableSyncResult', () => {
  it('is the failed result of a write that reached no backend, built fresh on every call', () => {
    const first = backendUnavailableSyncResult();

    expect(first).toEqual({
      success: false,
      conflicts: [],
      newVersions: new Map(),
      error: BACKEND_NOT_AVAILABLE_ERROR,
    });
    expect(backendUnavailableSyncResult().conflicts).not.toBe(first.conflicts);
    expect(backendUnavailableSyncResult().newVersions).not.toBe(first.newVersions);
  });
});

class StubAdapter extends BaseConfigAdapter {
  async getAll(): Promise<Map<ConfigKey, ConfigRecord>> {
    return new Map();
  }

  async get(): Promise<null> {
    return null;
  }

  async sync(): Promise<SyncResult> {
    return backendUnavailableSyncResult();
  }
}

describe('BaseConfigAdapter device id', () => {
  afterEach(() => {
    localStorage.clear();
    vi.unstubAllGlobals();
  });

  it('is generated once per browser, stored, and reused by the next adapter', () => {
    const id = new StubAdapter().getDeviceId();

    expect(id).toMatch(/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/);
    expect(localStorage.getItem('config-device-id')).toBe(id);
    expect(new StubAdapter().getDeviceId()).toBe(id);
  });

  it('is "server" when there is no window to keep an id in', () => {
    vi.stubGlobal('window', undefined);

    expect(new StubAdapter().getDeviceId()).toBe('server');
  });
});
