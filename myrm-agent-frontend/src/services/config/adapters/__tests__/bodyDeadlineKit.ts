/**
 * Shared fixtures for tests of code that faces an answer whose headers arrived but whose body did not: it stalls until
 * its request is aborted, or the connection breaks while it is read.
 */

import { expect, vi } from 'vitest';
import { RESPONSE_BODY_DEADLINE_MS } from '@/services/config/adapters/BaseAdapter';

/** What reading the JSON of an answer does; `signal` is the abort signal of the request that produced the answer. */
export type BodyRead = (signal: AbortSignal) => Promise<unknown>;

const abortError = () => new DOMException('The operation was aborted.', 'AbortError');

/** The `fetch` double of an answer whose headers arrive with `status`; `body` is what reading its JSON does. */
export const answerWithBody =
  (status: number, body: BodyRead) =>
  (_input: RequestInfo | URL, init?: RequestInit): Promise<Response> =>
    Promise.resolve({
      ok: status >= 200 && status < 300,
      status,
      statusText: `HTTP ${status}`,
      json: () => body(init?.signal ?? new AbortController().signal),
    } as Response);

/** A body that never ends until its request is aborted; a browser then rejects the read with an `AbortError`. */
export const stalledBody: BodyRead = (signal) =>
  new Promise<never>((_resolve, reject) => {
    if (signal.aborted) {
      reject(abortError());
      return;
    }
    signal.addEventListener('abort', () => reject(abortError()), { once: true });
  });

/** A body the connection broke off in the middle of; browsers reject such a read with a `TypeError`. */
export const brokenBody: BodyRead = () => Promise.reject(new TypeError('network error'));

/** A body that arrived complete but is not JSON. */
export const malformedBody: BodyRead = () => Promise.reject(new SyntaxError('Unexpected token < in JSON'));

/** The abort signal the code under test handed to its n-th `fetch` call. */
export function signalOfRequest(call = 0): AbortSignal {
  const signal = vi.mocked(fetch).mock.calls[call]?.[1]?.signal;
  if (!signal) {
    throw new Error(`fetch call ${call} carried no abort signal`);
  }
  return signal;
}

/**
 * Lets the deadline of an answer body run out under fake timers: the call must still be pending a millisecond before
 * the deadline and settled right after it.
 */
export async function runOutBodyDeadline(call: Promise<unknown>): Promise<void> {
  const settled = vi.fn();
  call.then(settled, settled);
  await vi.advanceTimersByTimeAsync(RESPONSE_BODY_DEADLINE_MS - 1);
  expect(settled).not.toHaveBeenCalled();
  await vi.advanceTimersByTimeAsync(1);
  expect(settled).toHaveBeenCalledTimes(1);
}
