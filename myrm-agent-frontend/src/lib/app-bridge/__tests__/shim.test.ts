import { describe, expect, it } from 'vitest';
import { injectAppBridge } from '../shim';

interface Posted {
  jsonrpc: string;
  id: number;
  method: string;
  params: Record<string, unknown>;
}

interface MyrmClient {
  notify(options?: { title?: string; body?: string }): Promise<void>;
  fetch(url: string, init?: { method?: string; headers?: Record<string, string>; body?: unknown }): Promise<Response>;
}

/** Runs the injected script against a stand-in window whose parent records what the page sends. */
function bootShim() {
  const html = injectAppBridge('<h1>page</h1>');
  const code = html.slice(html.indexOf('<script>') + '<script>'.length, html.indexOf('</script>'));
  const parent = {
    posted: [] as Posted[],
    postMessage(message: Posted) {
      this.posted.push(message);
    },
  };
  const listeners: Array<(event: { source: unknown; data: unknown }) => void> = [];
  const fakeWindow: Record<string, unknown> = {
    parent,
    addEventListener: (_type: string, listener: (event: { source: unknown; data: unknown }) => void) =>
      listeners.push(listener),
  };
  new Function('window', code)(fakeWindow);
  return {
    html,
    myrm: fakeWindow.myrm as MyrmClient,
    parent,
    deliver: (data: unknown, source: unknown = parent) => listeners.forEach((listener) => listener({ source, data })),
  };
}

describe('injectAppBridge', () => {
  it('puts the client before the page so the page can use it while it loads', () => {
    const { html } = bootShim();
    expect(html.startsWith('<script>')).toBe(true);
    expect(html.endsWith('<h1>page</h1>')).toBe(true);
  });

  it('defines a frozen window.myrm', () => {
    const { myrm } = bootShim();
    expect(Object.isFrozen(myrm)).toBe(true);
    expect(Object.keys(myrm).sort()).toEqual(['fetch', 'notify']);
  });
});

describe('myrm.notify', () => {
  it('sends a JSON-RPC request to the parent and resolves with nothing', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.notify({ title: 'Rain soon', body: 'In 10 minutes' });
    const [sent] = parent.posted;
    expect(sent).toMatchObject({
      jsonrpc: '2.0',
      method: 'notify',
      params: { title: 'Rain soon', body: 'In 10 minutes' },
    });
    deliver({ jsonrpc: '2.0', id: sent.id, result: null });

    await expect(done).resolves.toBeUndefined();
  });

  it('rejects with the reason as error.code', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.notify({ title: 't' });
    deliver({
      jsonrpc: '2.0',
      id: parent.posted[0].id,
      error: { code: -32000, message: 'no', data: { reason: 'permission_denied' } },
    });

    await expect(done).rejects.toMatchObject({ message: 'no', code: 'permission_denied' });
  });

  it('falls back to a generic code when the reply names no reason', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.notify({ title: 't' });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, error: { code: -1, message: 'odd' } });

    await expect(done).rejects.toMatchObject({ code: 'failed' });
  });
});

describe('myrm.fetch', () => {
  it('sends url, upper-cased method, plain headers and a string body', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.fetch('https://api.example.com/x', { method: 'post', headers: { 'X-A': '1' }, body: 5 });
    expect(parent.posted[0].params).toEqual({
      url: 'https://api.example.com/x',
      method: 'POST',
      headers: { 'x-a': '1' },
      body: '5',
    });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: { status: 200, headers: {}, body: '' } });
    await done;
  });

  it('defaults to GET without a body', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.fetch('https://api.example.com/x');
    expect(parent.posted[0].params).toEqual({
      url: 'https://api.example.com/x',
      method: 'GET',
      headers: {},
      body: null,
    });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: { status: 200, headers: {}, body: '' } });
    await done;
  });

  it('resolves with a real Response', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.fetch('https://api.example.com/x');
    deliver({
      jsonrpc: '2.0',
      id: parent.posted[0].id,
      result: { status: 404, headers: { 'content-type': 'application/json' }, body: '{"a":1}' },
    });
    const response = await done;

    expect(response.status).toBe(404);
    expect(response.ok).toBe(false);
    expect(response.headers.get('content-type')).toBe('application/json');
    expect(await response.json()).toEqual({ a: 1 });
  });

  it.each([204, 205, 304])('builds a body-less Response for status %i', async (status) => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.fetch('https://api.example.com/x');
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: { status, headers: {}, body: '' } });

    expect((await done).status).toBe(status);
  });

  it('skips a response header the browser would refuse instead of failing the call', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.fetch('https://api.example.com/x');
    deliver({
      jsonrpc: '2.0',
      id: parent.posted[0].id,
      result: { status: 200, headers: { 'bad name': 'x', 'x-ok': '1' }, body: 'hi' },
    });
    const response = await done;

    expect(response.headers.get('x-ok')).toBe('1');
    expect(await response.text()).toBe('hi');
  });
});

describe('replies', () => {
  it('keeps concurrent calls apart by id', async () => {
    const { myrm, parent, deliver } = bootShim();

    const first = myrm.fetch('https://a.example.com');
    const second = myrm.fetch('https://b.example.com');
    deliver({ jsonrpc: '2.0', id: parent.posted[1].id, result: { status: 200, headers: {}, body: 'second' } });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: { status: 200, headers: {}, body: 'first' } });

    expect(await (await first).text()).toBe('first');
    expect(await (await second).text()).toBe('second');
  });

  it('ignores a reply that does not come from the parent window', async () => {
    const { myrm, parent, deliver } = bootShim();

    let settled = false;
    const done = myrm.notify({ title: 't' }).then(() => {
      settled = true;
    });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: null }, { some: 'other window' });
    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(settled).toBe(false);

    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: null });
    await done;
    expect(settled).toBe(true);
  });

  it('ignores replies for ids it never issued and settles each call once', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.notify({ title: 't' });
    deliver({ jsonrpc: '2.0', id: 999, result: null });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: null });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, error: { message: 'late', data: { reason: 'failed' } } });

    await expect(done).resolves.toBeUndefined();
  });
});
