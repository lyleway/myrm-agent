/**
 * Shared fixtures of the adapter tests for an answer whose headers arrived but whose body did not: it stalls, or the
 * connection breaks while it is read.
 */

import { expect, vi } from 'vitest';
import { RESPONSE_BODY_DEADLINE_MS } from '@/services/config/adapters/BaseAdapter';

/** An answer whose headers arrived with `status`; `body` is what reading its JSON does. */
export const answerWithBody = (status: number, body: () => Promise<unknown>): Response =>
  ({ ok: status >= 200 && status < 300, status, statusText: `HTTP ${status}`, json: body }) as Response;

/** A body that never ends: the backend sent its headers and went quiet. */
export const stalledBody = (): Promise<never> => new Promise<never>(() => undefined);

/** A body the connection broke off in the middle of; browsers reject such a read with a `TypeError`. */
export const brokenBody = (): Promise<never> => Promise.reject(new TypeError('network error'));

/** A body that arrived complete but is not JSON. */
export const malformedBody = (): Promise<never> => Promise.reject(new SyntaxError('Unexpected token < in JSON'));

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
