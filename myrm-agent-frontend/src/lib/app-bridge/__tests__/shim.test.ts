import { describe, expect, it } from 'vitest';
import { injectAppBridge } from '../shim';

interface Posted {
  jsonrpc: string;
  id: number;
  method: string;
  params: Record<string, unknown>;
}

interface MyrmClient {
  on(name: string, handler: (data: unknown) => void): () => void;
  tray: { set(options?: { title?: string; tooltip?: string }): Promise<void>; clear(): Promise<void> };
  shortcut: { register(accelerator: string): Promise<void>; unregister(accelerator: string): Promise<void> };
  float(on: boolean): Promise<void>;
  llm: { complete(options: string | { prompt?: unknown; system?: string; maxTokens?: number }): Promise<string> };
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
    expect(Object.keys(myrm).sort()).toEqual(['fetch', 'float', 'llm', 'notify', 'on', 'shortcut', 'tray']);
    expect(Object.isFrozen(myrm.llm)).toBe(true);
    expect(Object.isFrozen(myrm.tray)).toBe(true);
    expect(Object.isFrozen(myrm.shortcut)).toBe(true);
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

describe('the desktop calls', () => {
  it.each<[string, (myrm: MyrmClient) => Promise<void>, string, Record<string, unknown>]>([
    [
      'tray.set',
      (myrm) => myrm.tray.set({ title: '3 unread', tooltip: 'Inbox' }),
      'tray.set',
      { title: '3 unread', tooltip: 'Inbox' },
    ],
    ['tray.set without options', (myrm) => myrm.tray.set(), 'tray.set', {}],
    ['tray.clear', (myrm) => myrm.tray.clear(), 'tray.clear', {}],
    ['shortcut.register', (myrm) => myrm.shortcut.register('Alt+F1'), 'shortcut.register', { accelerator: 'Alt+F1' }],
    [
      'shortcut.unregister',
      (myrm) => myrm.shortcut.unregister('Alt+F1'),
      'shortcut.unregister',
      { accelerator: 'Alt+F1' },
    ],
    ['float(true)', (myrm) => myrm.float(true), 'float.set', { on: true }],
    ['float with a truthy value', (myrm) => myrm.float('yes' as unknown as boolean), 'float.set', { on: true }],
    ['float(false)', (myrm) => myrm.float(false), 'float.set', { on: false }],
  ])('%s sends its request and resolves with nothing', async (_name, run, method, params) => {
    const { myrm, parent, deliver } = bootShim();

    const done = run(myrm);
    expect(parent.posted[0]).toMatchObject({ jsonrpc: '2.0', method, params });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: null });

    await expect(done).resolves.toBeUndefined();
  });

  it('rejects with the reason when the host refuses', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.shortcut.register('Alt+F1');
    deliver({
      jsonrpc: '2.0',
      id: parent.posted[0].id,
      error: { code: -32000, message: 'taken', data: { reason: 'shortcut_conflict' } },
    });

    await expect(done).rejects.toMatchObject({ code: 'shortcut_conflict' });
  });
});

describe('myrm.on', () => {
  const event = (name: string, data: unknown) => ({ jsonrpc: '2.0', method: 'myrm/event', params: { name, data } });

  it('hands the host’s event to the handlers registered for that name', () => {
    const { myrm, deliver } = bootShim();
    const seen: unknown[] = [];
    myrm.on('shortcut', (data) => seen.push(data));
    myrm.on('permission-revoked', () => seen.push('wrong'));

    deliver(event('shortcut', { accelerator: 'Alt+F1' }));

    expect(seen).toEqual([{ accelerator: 'Alt+F1' }]);
  });

  it('stops delivering after the returned function is called', () => {
    const { myrm, deliver } = bootShim();
    const seen: unknown[] = [];
    const stop = myrm.on('shortcut', (data) => seen.push(data));

    deliver(event('shortcut', 1));
    stop();
    deliver(event('shortcut', 2));

    expect(seen).toEqual([1]);
  });

  it('keeps one failing handler from silencing the others', () => {
    const { myrm, deliver } = bootShim();
    const seen: unknown[] = [];
    myrm.on('shortcut', () => {
      throw new Error('page bug');
    });
    myrm.on('shortcut', (data) => seen.push(data));

    deliver(event('shortcut', 1));

    expect(seen).toEqual([1]);
  });

  it('ignores an event that does not come from the parent window', () => {
    const { myrm, deliver } = bootShim();
    const seen: unknown[] = [];
    myrm.on('shortcut', (data) => seen.push(data));

    deliver(event('shortcut', 1), { some: 'other window' });

    expect(seen).toEqual([]);
  });

  it('accepts only a function as a handler', () => {
    const { myrm } = bootShim();

    expect(() => myrm.on('shortcut', 'nope' as unknown as () => void)).not.toThrow();
  });
});

describe('myrm.llm.complete', () => {
  it('sends the prompt, system text and output cap and resolves with the answer text', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.llm.complete({ prompt: 'Weather?', system: 'Be brief', maxTokens: 64 });
    expect(parent.posted[0]).toMatchObject({
      method: 'ai.complete',
      params: { prompt: 'Weather?', system: 'Be brief', maxTokens: 64 },
    });
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: { text: 'Sunny' } });

    await expect(done).resolves.toBe('Sunny');
  });

  it('takes a plain string as the prompt', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.llm.complete('Weather?');
    expect(parent.posted[0].params.prompt).toBe('Weather?');
    deliver({ jsonrpc: '2.0', id: parent.posted[0].id, result: { text: 'Sunny' } });

    await expect(done).resolves.toBe('Sunny');
  });

  it('leaves a missing prompt for the host to refuse instead of sending the text "undefined"', async () => {
    const { myrm, parent } = bootShim();

    void myrm.llm.complete({});

    expect(parent.posted[0].params.prompt).toBeUndefined();
  });

  it('rejects with the host reason as error.code', async () => {
    const { myrm, parent, deliver } = bootShim();

    const done = myrm.llm.complete('hi');
    deliver({
      jsonrpc: '2.0',
      id: parent.posted[0].id,
      error: { code: -32000, message: 'The daily budget leaves no room', data: { reason: 'budget_exceeded' } },
    });

    await expect(done).rejects.toMatchObject({ code: 'budget_exceeded', message: 'The daily budget leaves no room' });
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
