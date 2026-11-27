import { describe, expect, it } from 'vitest';
import type { CapabilityStatus } from '../declaration';
import { okReply, reasonOf, request, setup, SHA } from './hostHarness';

describe('foreign messages', () => {
  it('returns null for anything that is not a JSON-RPC request', async () => {
    const { handle, activity } = setup();
    expect(await handle({ type: 'widget-resize', height: 300 })).toBeNull();
    expect(await handle('hello')).toBeNull();
    expect(activity).toEqual([]);
  });

  it('answers an unknown method with method_not_found and echoes the id', async () => {
    const { handle } = setup();
    const reply = await handle(request('camera', {}, 'req-9'));
    expect(reply).toMatchObject({ id: 'req-9', error: { code: -32601 } });
    expect(reasonOf(reply)).toBe('method_not_found');
  });
});

describe('notify', () => {
  it('shows the notification under the app name and logs it', async () => {
    const { handle, showNotification, activity } = setup();

    const reply = await handle(request('notify', { title: 'Rain soon', body: 'In 10 minutes' }));

    expect(reply).toEqual({ jsonrpc: '2.0', id: 1, result: null });
    expect(showNotification).toHaveBeenCalledWith('Weather · Rain soon', 'In 10 minutes');
    expect(activity).toEqual([{ capability: 'notify', outcome: 'ok', detail: null }]);
  });

  it.each<[CapabilityStatus, string]>([
    ['ask', 'permission_denied'],
    ['denied', 'permission_denied'],
    ['undeclared', 'not_declared'],
  ])('refuses while the capability is %s', async (status, reason) => {
    const { handle, showNotification, activity } = setup({ notify: status });

    const reply = await handle(request('notify', { title: 't' }));

    expect(reasonOf(reply)).toBe(reason);
    expect(showNotification).not.toHaveBeenCalled();
    expect(activity).toEqual([{ capability: 'notify', outcome: 'denied', detail: null }]);
  });

  it('rejects malformed params without showing anything', async () => {
    const { handle, showNotification } = setup();
    expect(reasonOf(await handle(request('notify', { title: '' })))).toBe('invalid_params');
    expect(showNotification).not.toHaveBeenCalled();
  });

  it('reports a notification that could not be shown as failed', async () => {
    const { handle, showNotification, activity } = setup();
    showNotification.mockRejectedValue(new Error('no permission'));

    expect(reasonOf(await handle(request('notify', { title: 't' })))).toBe('failed');
    expect(activity).toEqual([{ capability: 'notify', outcome: 'error', detail: null }]);
  });

  it('allows five a minute, logs the first refusal once and recovers when the window moves on', async () => {
    const { handle, clock, activity, showNotification } = setup();
    for (let index = 0; index < 5; index += 1) {
      expect(await handle(request('notify', { title: `n${index}` }, index))).toMatchObject({ result: null });
    }

    expect(reasonOf(await handle(request('notify', { title: 'x' }, 10)))).toBe('rate_limited');
    expect(reasonOf(await handle(request('notify', { title: 'x' }, 11)))).toBe('rate_limited');
    expect(showNotification).toHaveBeenCalledTimes(5);
    expect(activity.filter((entry) => entry.outcome === 'rate_limited')).toHaveLength(1);

    clock.at += 61_000;
    expect(await handle(request('notify', { title: 'again' }, 12))).toMatchObject({ result: null });
  });

  it('counts refused calls against the limit so a denied page cannot flood the log', async () => {
    const { handle, activity } = setup({ notify: 'denied' });
    for (let index = 0; index < 50; index += 1) {
      await handle(request('notify', { title: 't' }, index));
    }
    expect(activity.filter((entry) => entry.outcome === 'denied')).toHaveLength(5);
    expect(activity.filter((entry) => entry.outcome === 'rate_limited')).toHaveLength(1);
  });
});

describe('fetch', () => {
  const GOOD = { url: 'https://api.weather.com/v1/now?secret=1', method: 'GET' };

  it('forwards the request with the version and hands back status, headers and body', async () => {
    const { handle, proxyFetch, activity } = setup();
    proxyFetch.mockResolvedValue(okReply());

    const reply = await handle(request('fetch', GOOD));

    expect(proxyFetch).toHaveBeenCalledWith({ ...GOOD, headers: {}, body: null, version_sha256: SHA });
    expect(reply).toEqual({
      jsonrpc: '2.0',
      id: 1,
      result: { status: 200, headers: { 'content-type': 'application/json' }, body: '{"t":1}' },
    });
    expect(activity).toEqual([]);
  });

  it('passes an upstream error status through as a result, not a failure', async () => {
    const { handle, proxyFetch } = setup();
    proxyFetch.mockResolvedValue({ ...okReply('nope'), status: 503 });

    expect(await handle(request('fetch', GOOD))).toMatchObject({ result: { status: 503, body: 'nope' } });
  });

  it.each<[CapabilityStatus, string]>([
    ['ask', 'permission_denied'],
    ['denied', 'permission_denied'],
    ['undeclared', 'not_declared'],
  ])('refuses before the server while the capability is %s, logging only the host name', async (status, reason) => {
    const { handle, proxyFetch, activity } = setup({ fetch: status });

    const reply = await handle(request('fetch', GOOD));

    expect(reasonOf(reply)).toBe(reason);
    expect(proxyFetch).not.toHaveBeenCalled();
    expect(activity).toEqual([{ capability: 'fetch', outcome: 'denied', detail: 'api.weather.com' }]);
  });

  it('logs no detail for a url that has no host', async () => {
    const { handle, activity } = setup({ fetch: 'denied' });
    await handle(request('fetch', { url: 'not a url' }));
    expect(activity).toEqual([{ capability: 'fetch', outcome: 'denied', detail: null }]);
  });

  it('rejects malformed params once the capability is allowed', async () => {
    const { handle, proxyFetch } = setup();
    expect(reasonOf(await handle(request('fetch', { url: 'https://a.example.com', method: 'DELETE' })))).toBe(
      'invalid_params',
    );
    expect(proxyFetch).not.toHaveBeenCalled();
  });

  it.each([
    ['host_not_granted', 'host_not_granted'],
    ['rate_limited', 'rate_limited'],
    ['bad_request', 'bad_request'],
    ['blocked', 'blocked'],
    ['response_too_large', 'response_too_large'],
    ['timeout', 'timeout'],
    ['upstream_error', 'upstream_error'],
    ['something_new', 'failed'],
  ])('maps the server refusal %s to %s', async (code, reason) => {
    const { handle, proxyFetch } = setup();
    proxyFetch.mockResolvedValue({ ...okReply(), ok: false, status: null, body: '', code, message: 'refused' });

    const reply = await handle(request('fetch', GOOD));

    expect(reasonOf(reply)).toBe(reason);
    expect(reply).toMatchObject({ error: { message: 'refused' } });
  });

  it('reports a failed request to the server as failed without leaking the cause', async () => {
    const { handle, proxyFetch } = setup();
    proxyFetch.mockRejectedValue(new Error('connect ECONNREFUSED 127.0.0.1:8000 token=abc'));

    const reply = await handle(request('fetch', GOOD));

    expect(reasonOf(reply)).toBe('failed');
    expect(JSON.stringify(reply)).not.toContain('ECONNREFUSED');
  });

  it('bounds denied-call chatter at 120 a minute', async () => {
    const { handle, activity } = setup({ fetch: 'denied' });
    for (let index = 0; index < 200; index += 1) {
      await handle(request('fetch', GOOD, index));
    }
    expect(activity.filter((entry) => entry.outcome === 'denied')).toHaveLength(120);
    expect(activity.filter((entry) => entry.outcome === 'rate_limited')).toHaveLength(1);
  });

  it('keeps the two capabilities on separate budgets', async () => {
    const { handle, proxyFetch } = setup();
    proxyFetch.mockResolvedValue(okReply());
    for (let index = 0; index < 6; index += 1) {
      await handle(request('notify', { title: 't' }, index));
    }

    expect(await handle(request('fetch', GOOD, 100))).toMatchObject({ result: { status: 200 } });
  });
});
