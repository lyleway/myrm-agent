import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError, apiRequest } from '@/lib/api';
import { COMPACT_FOCUS_TOPIC_MAX_LENGTH } from '@/lib/constants/compact-focus-topic';
import {
  COMPACT_POLL_FIRST_INTERVAL_MS,
  COMPACT_POLL_MAX_CONSECUTIVE_ERRORS,
  COMPACT_POLL_MAX_INTERVAL_MS,
  COMPACT_RUN_DEADLINE_MS,
  type CompactResult,
  compactChat,
  describeCompactSkip,
  runManualCompaction,
} from '@/services/contextCompaction';
import en from '../../../locales/en.json';
import zh from '../../../locales/zh.json';

const refreshCompactionState = vi.hoisted(() => vi.fn());

vi.mock('@/lib/api', () => ({
  API_BASE_URL: '',
  ApiError: class ApiError extends Error {
    constructor(
      message: string,
      public code: number = 50001,
      public details: unknown[] = [],
      public traceId?: string,
      public businessCode?: string,
    ) {
      super(message);
      this.name = 'ApiError';
    }
  },
  apiRequest: vi.fn(),
  fetchWithTimeout: vi.fn(),
}));

vi.mock('@/store/useChatStore', () => ({ default: { getState: () => ({ refreshCompactionState }) } }));

const apiRequestMock = vi.mocked(apiRequest);
const RARE_HAN = String.fromCodePoint(0x20bb7);
const RUN_ENDPOINT = '/chats/chat-1/compact/run';

const RESULT: CompactResult = { compacted: true, message_count: 4, tokens_saved: 900, reason: null };
const RUNNING = { status: 'running' };
const succeeded = (result: CompactResult = RESULT) => ({ status: 'succeeded', result });

/** What the server answers to one poll: a run snapshot, or a failed request. */
type Poll = unknown;

/**
 * The server for one `compactChat`: it accepts the request (POST), then answers each poll with the next of `polls`
 * (the last one repeats). A poll that is an `Error` is a request that failed; `onPoll` is told of each poll asked.
 */
function serve(polls: readonly Poll[], onPoll?: () => void): void {
  let next = 0;
  apiRequestMock.mockImplementation(async (endpoint: string) => {
    if (endpoint !== RUN_ENDPOINT) {
      return RUNNING;
    }
    onPoll?.();
    const poll = polls[Math.min(next, polls.length - 1)];
    next += 1;
    if (poll instanceof Error) {
      throw poll;
    }
    return poll;
  });
}

/** Lets the timers of a started call run, so the polling it sleeps between needs no real waiting. */
async function settle<T>(started: Promise<T>): Promise<T> {
  const outcome = started.then(
    (value) => ({ value }),
    (error: unknown) => ({ error }),
  );
  await vi.runAllTimersAsync();
  const done = await outcome;
  if ('error' in done) {
    throw done.error;
  }
  return done.value;
}

/** The calls of `apiRequest` to the run endpoint. */
const pollCalls = () => apiRequestMock.mock.calls.filter(([endpoint]) => endpoint === RUN_ENDPOINT);

/** The focus topic the request carried, or undefined when it carried no body. */
function sentTopic(): string | undefined {
  const [, init] = apiRequestMock.mock.calls[0];
  return init?.body === undefined ? undefined : (JSON.parse(String(init.body)) as { focus_topic: string }).focus_topic;
}

describe('compactChat', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    apiRequestMock.mockReset();
    serve([succeeded()]);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts the compaction with one request and reads the outcome from the run endpoint', async () => {
    serve([RUNNING, RUNNING, succeeded()]);

    await expect(settle(compactChat('chat-1'))).resolves.toEqual(RESULT);

    expect(apiRequestMock.mock.calls.map(([endpoint]) => endpoint)).toEqual([
      '/chats/chat-1/compact',
      RUN_ENDPOINT,
      RUN_ENDPOINT,
      RUN_ENDPOINT,
    ]);
  });

  it('sends a topic within the limit as it is, without a page-side timeout on the request that only starts the run', async () => {
    await settle(compactChat('chat-1', 'billing decisions'));

    expect(apiRequestMock).toHaveBeenNthCalledWith(1, '/chats/chat-1/compact', {
      method: 'POST',
      body: JSON.stringify({ focus_topic: 'billing decisions' }),
      headers: { 'Content-Type': 'application/json' },
    });
  });

  it.each([undefined, ''])('sends no body when the topic is %j', async (topic) => {
    await settle(compactChat('chat-1', topic));

    expect(apiRequestMock).toHaveBeenNthCalledWith(1, '/chats/chat-1/compact', { method: 'POST' });
  });

  it('leaves the request that starts the run to the default error handling: only the polls are silent', async () => {
    await settle(compactChat('chat-1'));

    const [, startInit] = apiRequestMock.mock.calls[0];
    expect(startInit).not.toHaveProperty('silent');
    expect(pollCalls().length).toBeGreaterThan(0);
    for (const [, init] of pollCalls()) {
      expect(init).toMatchObject({ silent: true });
    }
  });

  it('does not ask again before the first interval has passed, then asks less and less eagerly up to a cap', async () => {
    const askedAt: number[] = [];
    serve([...Array.from({ length: 12 }, () => RUNNING), succeeded()], () => askedAt.push(Date.now()));
    const startedAt = Date.now();

    await settle(compactChat('chat-1'));

    const gaps = askedAt.map((at, index) => at - (index === 0 ? startedAt : askedAt[index - 1]));
    expect(gaps[0]).toBe(COMPACT_POLL_FIRST_INTERVAL_MS);
    expect(gaps).toEqual([...gaps].sort((a, b) => a - b));
    expect(gaps.at(-1)).toBe(COMPACT_POLL_MAX_INTERVAL_MS);
    expect(Math.max(...gaps)).toBe(COMPACT_POLL_MAX_INTERVAL_MS);
  });

  it('tolerates failed polls within the budget of consecutive failures', async () => {
    const failed = new Error('offline');
    serve([...Array.from({ length: COMPACT_POLL_MAX_CONSECUTIVE_ERRORS - 1 }, () => failed), succeeded()]);

    await expect(settle(compactChat('chat-1'))).resolves.toEqual(RESULT);
  });

  it('counts only consecutive failures: an answer in between restores the budget', async () => {
    const failed = new Error('offline');
    const burst = Array.from({ length: COMPACT_POLL_MAX_CONSECUTIVE_ERRORS - 1 }, () => failed);
    serve([...burst, RUNNING, ...burst, succeeded()]);

    await expect(settle(compactChat('chat-1'))).resolves.toEqual(RESULT);
  });

  it('gives up with the last error once the polls have failed as many times in a row as the budget allows', async () => {
    serve([
      new Error('offline 1'),
      new Error('offline 2'),
      new Error('offline 3'),
      new Error('offline 4'),
      new Error('offline 5'),
    ]);

    await expect(settle(compactChat('chat-1'))).rejects.toThrow('offline 5');

    expect(pollCalls()).toHaveLength(COMPACT_POLL_MAX_CONSECUTIVE_ERRORS);
  });

  it('treats a run the server does not know as failed at once, because no later poll can find it', async () => {
    const forgotten = new ApiError('Compaction run not found', 404);
    serve([forgotten, succeeded()]);

    await expect(settle(compactChat('chat-1'))).rejects.toBe(forgotten);

    expect(pollCalls()).toHaveLength(1);
  });

  it('turns a failed run into an ApiError that keeps the status and the business code, without polling on', async () => {
    serve([
      RUNNING,
      {
        status: 'failed',
        error: { http_status: 500, code: 53001, message: 'Compact chat context failed: AI model error' },
      },
    ]);

    const failure = await settle(compactChat('chat-1')).catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({
      message: 'Compact chat context failed: AI model error',
      code: 500,
      businessCode: '53001',
    });
    expect(pollCalls()).toHaveLength(2);
  });

  it('reads a declined compaction as a finished one: the reason is for the caller to word', async () => {
    const declined = { ...RESULT, compacted: false, tokens_saved: 0, reason: 'no_compactable_messages' };
    serve([succeeded(declined)]);

    await expect(settle(compactChat('chat-1'))).resolves.toEqual(declined);
  });

  it('gives up on a run that is still going when its deadline has passed', async () => {
    serve([RUNNING]);
    const startedAt = Date.now();

    await expect(settle(compactChat('chat-1'))).rejects.toThrow('did not finish');

    const waited = Date.now() - startedAt;
    expect(waited).toBeGreaterThanOrEqual(COMPACT_RUN_DEADLINE_MS);
    expect(waited).toBeLessThan(COMPACT_RUN_DEADLINE_MS + COMPACT_POLL_MAX_INTERVAL_MS);
  });

  it("waits longer than the server may work on a compaction, so the answer read is the server's and not the page's deadline", () => {
    const SERVER_TOTAL_CEILING_MS = 600_000; // COMPACT_TOTAL_CEILING_S in the server's compact/_constants.py

    expect(COMPACT_RUN_DEADLINE_MS).toBeGreaterThan(SERVER_TOTAL_CEILING_MS);
  });

  it('cuts a longer topic to the limit the server accepts, so the request is not refused', async () => {
    await settle(compactChat('chat-1', 'x'.repeat(COMPACT_FOCUS_TOPIC_MAX_LENGTH + 60)));

    expect(sentTopic()).toBe('x'.repeat(COMPACT_FOCUS_TOPIC_MAX_LENGTH));
  });

  it('cuts by character, so a rare han character on the limit is not split in half', async () => {
    await settle(compactChat('chat-1', `a${RARE_HAN.repeat(COMPACT_FOCUS_TOPIC_MAX_LENGTH + 10)}`));

    expect(sentTopic()).toBe(`a${RARE_HAN.repeat(COMPACT_FOCUS_TOPIC_MAX_LENGTH - 1)}`);
  });
});

describe('runManualCompaction', () => {
  const compacted = { compacted: true, message_count: 4, tokens_saved: 900, reason: null, backup_path: '/tmp/b.json' };

  beforeEach(() => {
    vi.useFakeTimers();
    apiRequestMock.mockReset();
    serve([succeeded(compacted)]);
    refreshCompactionState.mockReset();
    refreshCompactionState.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it('hands a compaction that freed tokens to the store, so every view of the chat catches up', async () => {
    await expect(settle(runManualCompaction('chat-1', 'billing'))).resolves.toEqual(compacted);

    expect(sentTopic()).toBe('billing');
    expect(refreshCompactionState).toHaveBeenCalledWith('chat-1', { tokensSaved: 900, snapshotPath: '/tmp/b.json' });
  });

  it('records no snapshot path when the server kept no backup', async () => {
    serve([succeeded({ ...compacted, backup_path: null })]);

    await settle(runManualCompaction('chat-1'));

    expect(refreshCompactionState).toHaveBeenCalledWith('chat-1', { tokensSaved: 900, snapshotPath: undefined });
  });

  it('leaves the store alone when nothing was compacted', async () => {
    const skipped = { ...compacted, compacted: false, tokens_saved: 0, reason: 'no_compactable_messages' };
    serve([succeeded(skipped)]);

    await expect(settle(runManualCompaction('chat-1'))).resolves.toEqual(skipped);

    expect(refreshCompactionState).not.toHaveBeenCalled();
  });

  it('passes a request that could not even start the run on and leaves the store alone', async () => {
    apiRequestMock.mockRejectedValue(new Error('offline'));

    await expect(settle(runManualCompaction('chat-1'))).rejects.toThrow('offline');

    expect(refreshCompactionState).not.toHaveBeenCalled();
  });

  it('passes a failed run on and leaves the store alone', async () => {
    serve([{ status: 'failed', error: { http_status: 500, code: 53001, message: 'Compact chat context failed' } }]);

    await expect(settle(runManualCompaction('chat-1'))).rejects.toBeInstanceOf(ApiError);

    expect(refreshCompactionState).not.toHaveBeenCalled();
  });

  it('still reports a compaction that happened when the store could not take it', async () => {
    const logged = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    refreshCompactionState.mockRejectedValue(new Error('sync failed'));

    await expect(settle(runManualCompaction('chat-1'))).resolves.toEqual(compacted);

    expect(logged).toHaveBeenCalledOnce();
  });

  it.each([
    { reason: 'timeout', logged: true },
    { reason: 'persist_failed', logged: true },
    { reason: 'no_compactable_messages', logged: false },
    { reason: 'chat_not_found', logged: false },
    { reason: 'concurrent_compaction_in_progress', logged: false },
  ])('keeps the raw reason of a failure for the console only ($reason)', async ({ reason, logged }) => {
    const errorLog = vi.spyOn(console, 'error').mockImplementation(() => undefined);
    serve([succeeded({ ...compacted, compacted: false, tokens_saved: 0, reason })]);

    await settle(runManualCompaction('chat-1'));

    expect(errorLog.mock.calls).toEqual(logged ? [['[Compaction] failed:', reason]] : []);
  });
});

/** Every reason the compaction service reports for a request it did not run. */
const SKIP_CASES = [
  { reason: 'no_compactable_messages', messageKey: 'chat.compact.skip.nothing', severity: 'info' },
  { reason: 'too_few_messages', messageKey: 'chat.compact.skip.nothing', severity: 'info' },
  // A new chat has an id before its first message is sent; the server has no such chat yet, and it holds nothing.
  { reason: 'chat_not_found', messageKey: 'chat.compact.skip.nothing', severity: 'info' },
  { reason: 'concurrent_compaction_in_progress', messageKey: 'chat.compact.skip.busy', severity: 'info' },
  { reason: 'summarize_circuit_open', messageKey: 'chat.compact.skip.paused', severity: 'warning' },
  { reason: 'compression_anti_thrash_active', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'timeout', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'persist_failed', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'too_few_messages (2 < 4)', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'a_reason_added_later', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'constructor', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: '__proto__', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: '', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: null, messageKey: 'chat.compact.failed', severity: 'error' },
] as const;

/** The text a dotted key resolves to in a locale bundle, or undefined when the bundle has none. */
function copyOf(bundle: unknown, key: string): unknown {
  return key
    .split('.')
    .reduce<unknown>(
      (node, part) => (typeof node === 'object' && node !== null ? (node as Record<string, unknown>)[part] : undefined),
      bundle,
    );
}

describe('describeCompactSkip', () => {
  it.each(SKIP_CASES)('words $reason as $messageKey ($severity)', ({ reason, messageKey, severity }) => {
    expect(describeCompactSkip(reason)).toEqual({ messageKey, severity });
  });

  it.each([
    { locale: 'English', bundle: en },
    { locale: 'Chinese', bundle: zh },
  ])('has $locale copy for every notice', ({ bundle }) => {
    const keys = new Set(SKIP_CASES.map(({ reason }) => describeCompactSkip(reason).messageKey));

    expect([...keys].filter((key) => typeof copyOf(bundle, key) !== 'string')).toEqual([]);
  });
});
