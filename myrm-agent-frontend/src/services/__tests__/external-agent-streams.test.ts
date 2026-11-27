import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ fetchWithTimeout: vi.fn() }));

vi.mock('@/lib/api', () => ({ apiRequest: vi.fn(), fetchWithTimeout: mocks.fetchWithTimeout }));

import { streamExternalAgentInstall, streamExternalAgentLogin } from '@/services/external-agents';
import type { ExternalAgentAuthEvent } from '@/services/external-agents';

const encoder = new TextEncoder();

/** A response whose body delivers the given chunks one read at a time. */
function sseResponse(chunks: string[], init: ResponseInit = { status: 200 }): Response {
  const body = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(encoder.encode(chunk));
      }
      controller.close();
    },
  });
  return new Response(body, init);
}

const frame = (event: ExternalAgentAuthEvent) => `data: ${JSON.stringify(event)}\n\n`;

async function collect(stream: AsyncGenerator<ExternalAgentAuthEvent>): Promise<ExternalAgentAuthEvent[]> {
  const events: ExternalAgentAuthEvent[] = [];
  for await (const event of stream) {
    events.push(event);
  }
  return events;
}

describe('streamExternalAgentInstall', () => {
  beforeEach(() => {
    mocks.fetchWithTimeout.mockReset();
    localStorage.clear();
  });

  it('yields the installer events in order, reassembling frames split across chunks', async () => {
    const first = frame({ type: 'progress', message: 'Downloading Node.js' });
    const second = frame({ type: 'success', message: 'Installation complete' });
    const cut = Math.floor(second.length / 2);
    mocks.fetchWithTimeout.mockResolvedValue(sseResponse([first, second.slice(0, cut), second.slice(cut)]));

    expect(await collect(streamExternalAgentInstall('claude'))).toEqual([
      { type: 'progress', message: 'Downloading Node.js' },
      { type: 'success', message: 'Installation complete' },
    ]);
  });

  it('has no client-side deadline, so the server-side bounds are the only ones', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(sseResponse([]));

    await collect(streamExternalAgentInstall('claude'));

    const [url, , timeout] = mocks.fetchWithTimeout.mock.calls[0];
    expect(url).toBe('/external-agents/install/claude');
    expect(timeout).toBe(0);
  });

  it('encodes the backend name into the path', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(sseResponse([]));

    await collect(streamExternalAgentInstall('a/b c'));

    expect(mocks.fetchWithTimeout.mock.calls[0][0]).toBe('/external-agents/install/a%2Fb%20c');
  });

  it('forwards the abort signal and the bearer token', async () => {
    localStorage.setItem('auth_token', 'tok-1');
    mocks.fetchWithTimeout.mockResolvedValue(sseResponse([]));
    const controller = new AbortController();

    await collect(streamExternalAgentInstall('claude', controller.signal));

    const [, init] = mocks.fetchWithTimeout.mock.calls[0];
    expect(init.signal).toBe(controller.signal);
    expect(init.headers).toMatchObject({ Authorization: 'Bearer tok-1', Accept: 'text/event-stream' });
  });

  it('sends no authorization header without a stored token', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(sseResponse([]));

    await collect(streamExternalAgentInstall('claude'));

    expect(mocks.fetchWithTimeout.mock.calls[0][1].headers).not.toHaveProperty('Authorization');
  });

  it.each([401, 404, 500])('throws on HTTP %i instead of ending silently', async (status) => {
    mocks.fetchWithTimeout.mockResolvedValue(new Response('nope', { status }));

    await expect(collect(streamExternalAgentInstall('claude'))).rejects.toThrow(String(status));
  });

  it('skips malformed frames and stops at DONE', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(
      sseResponse([
        'data: {not json}\n\n',
        frame({ type: 'progress', message: 'kept' }),
        'data: [DONE]\n\n',
        frame({ type: 'progress', message: 'after done' }),
      ]),
    );

    expect(await collect(streamExternalAgentInstall('claude'))).toEqual([{ type: 'progress', message: 'kept' }]);
  });

  it('cancels the connection when the consumer stops early', async () => {
    const cancelled = vi.fn();
    const body = new ReadableStream<Uint8Array>({
      pull(controller) {
        controller.enqueue(encoder.encode(frame({ type: 'progress', message: 'tick' })));
      },
      cancel: cancelled,
    });
    mocks.fetchWithTimeout.mockResolvedValue(new Response(body, { status: 200 }));

    for await (const event of streamExternalAgentInstall('claude')) {
      expect(event.message).toBe('tick');
      break;
    }

    await vi.waitFor(() => expect(cancelled).toHaveBeenCalled());
  });
});

describe('streamExternalAgentLogin', () => {
  beforeEach(() => {
    mocks.fetchWithTimeout.mockReset();
    localStorage.clear();
  });

  it('delivers each event to the callback and returns when the stream ends', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(
      sseResponse([
        frame({ type: 'prompt', message: 'open', url: 'https://example.com/device', code: 'ABCD' }),
        frame({ type: 'success', message: 'done' }),
      ]),
    );
    const seen: ExternalAgentAuthEvent[] = [];

    await streamExternalAgentLogin({ command: 'codex', backend: 'codex', sessionId: 's1' }, (event) => {
      seen.push(event);
    });

    expect(seen.map((event) => event.type)).toEqual(['prompt', 'success']);
    const [url, init, timeout] = mocks.fetchWithTimeout.mock.calls[0];
    expect(url).toBe('/external-agents/auth/login');
    expect(timeout).toBe(0);
    expect(JSON.parse(init.body)).toEqual({ command: 'codex', backend: 'codex', sessionId: 's1' });
  });

  it('names the account to log in to when one is given', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(sseResponse([frame({ type: 'success', message: 'done' })]));

    await streamExternalAgentLogin({ command: 'claude', backend: 'claude', sessionId: 's1', account: 'work' }, () => {
      /* events are not under test */
    });

    expect(JSON.parse(mocks.fetchWithTimeout.mock.calls[0][1].body)).toEqual({
      command: 'claude',
      backend: 'claude',
      sessionId: 's1',
      account: 'work',
    });
  });

  it('says so when the browser that signs in is on another machine than the CLI', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(sseResponse([frame({ type: 'success', message: 'done' })]));

    await streamExternalAgentLogin({ command: 'codex', backend: 'codex', sessionId: 's1', remote: true }, () => {
      /* events are not under test */
    });

    expect(JSON.parse(mocks.fetchWithTimeout.mock.calls[0][1].body)).toEqual({
      command: 'codex',
      backend: 'codex',
      sessionId: 's1',
      remote: true,
    });
  });

  it('throws when the server refuses the login stream', async () => {
    mocks.fetchWithTimeout.mockResolvedValue(new Response('no', { status: 400 }));

    await expect(streamExternalAgentLogin({ command: 'x', sessionId: 's1' }, () => undefined)).rejects.toThrow('400');
  });
});
