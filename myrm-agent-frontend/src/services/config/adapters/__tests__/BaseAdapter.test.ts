import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import {
  RESPONSE_BODY_DEADLINE_MS,
  RESPONSE_HEADERS_DEADLINE_MS,
  fetchWithDeadlines,
} from '@/services/config/adapters/BaseAdapter';
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
