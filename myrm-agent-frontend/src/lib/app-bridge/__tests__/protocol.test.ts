import { describe, expect, it } from 'vitest';
import {
  BRIDGE_CAPABILITIES,
  BridgeError,
  isDesktopCapability,
  parseFetchParams,
  parseFloatParams,
  parseNotifyParams,
  parseRpcRequest,
  parseShortcutParams,
  parseTrayParams,
  rpcEvent,
  rpcFailure,
  rpcResult,
} from '../protocol';

describe('parseRpcRequest', () => {
  it('accepts a JSON-RPC 2.0 request with a numeric or string id', () => {
    expect(parseRpcRequest({ jsonrpc: '2.0', id: 1, method: 'notify', params: { title: 'x' } })).toEqual({
      id: 1,
      method: 'notify',
      params: { title: 'x' },
    });
    expect(parseRpcRequest({ jsonrpc: '2.0', id: 'a', method: 'fetch' })?.id).toBe('a');
  });

  it.each([
    ['null', null],
    ['a string', 'notify'],
    ['the theme update the host sends', { type: 'widget-theme-update', vars: {} }],
    ['a storage message', { type: 'widget-storage-batch', entries: [] }],
    ['another protocol version', { jsonrpc: '1.0', id: 1, method: 'notify' }],
    ['a notification without id', { jsonrpc: '2.0', method: 'notify' }],
    ['an object id', { jsonrpc: '2.0', id: {}, method: 'notify' }],
    ['a non-string method', { jsonrpc: '2.0', id: 1, method: 7 }],
  ])('ignores %s', (_label, data) => {
    expect(parseRpcRequest(data)).toBeNull();
  });
});

describe('replies', () => {
  it('wraps a result', () => {
    expect(rpcResult(3, { ok: true })).toEqual({ jsonrpc: '2.0', id: 3, result: { ok: true } });
  });

  it('maps standard reasons to the JSON-RPC codes and everything else to the application range', () => {
    const code = (reason: ConstructorParameters<typeof BridgeError>[0]) => {
      const reply = rpcFailure(1, new BridgeError(reason, 'm'));
      return 'error' in reply ? reply.error.code : null;
    };
    expect(code('method_not_found')).toBe(-32601);
    expect(code('invalid_params')).toBe(-32602);
    expect(code('permission_denied')).toBe(-32000);
    expect(code('host_not_granted')).toBe(-32000);
  });

  it('carries the reason where the page shim reads it', () => {
    const reply = rpcFailure(1, new BridgeError('rate_limited', 'slow down'));
    expect('error' in reply && reply.error).toEqual({
      code: -32000,
      message: 'slow down',
      data: { reason: 'rate_limited' },
    });
  });
});

describe('parseNotifyParams', () => {
  it('trims the title and defaults the body to empty', () => {
    expect(parseNotifyParams({ title: '  Rain soon ' })).toEqual({ title: 'Rain soon', body: '' });
    expect(parseNotifyParams({ title: 'T', body: 'B' })).toEqual({ title: 'T', body: 'B' });
  });

  it.each([
    ['no params', undefined],
    ['a missing title', { body: 'b' }],
    ['a blank title', { title: '   ' }],
    ['a title over 80 characters', { title: 'x'.repeat(81) }],
    ['a body over 200 characters', { title: 't', body: 'x'.repeat(201) }],
    ['a non-string body', { title: 't', body: 5 }],
    ['an array', ['t']],
  ])('rejects %s', (_label, params) => {
    expect(parseNotifyParams(params)).toBeNull();
  });

  it('accepts the limits themselves', () => {
    expect(parseNotifyParams({ title: 'x'.repeat(80), body: 'y'.repeat(200) })).not.toBeNull();
  });
});

describe('parseFetchParams', () => {
  it('fills the defaults', () => {
    expect(parseFetchParams({ url: 'https://api.example.com/a' })).toEqual({
      url: 'https://api.example.com/a',
      method: 'GET',
      headers: {},
      body: null,
    });
  });

  it('keeps method, headers and body', () => {
    expect(
      parseFetchParams({ url: 'https://a.example.com', method: 'POST', headers: { 'x-a': '1' }, body: '{}' }),
    ).toEqual({ url: 'https://a.example.com', method: 'POST', headers: { 'x-a': '1' }, body: '{}' });
  });

  it.each([
    ['no url', {}],
    ['a url over 2048 characters', { url: `https://a.example.com/${'x'.repeat(2048)}` }],
    ['a method the proxy does not offer', { url: 'https://a.example.com', method: 'DELETE' }],
    ['a lower-case method', { url: 'https://a.example.com', method: 'get' }],
    ['a non-string body', { url: 'https://a.example.com', body: { a: 1 } }],
    ['non-string header values', { url: 'https://a.example.com', headers: { a: 1 } }],
    ['array headers', { url: 'https://a.example.com', headers: ['a'] }],
  ])('rejects %s', (_label, params) => {
    expect(parseFetchParams(params)).toBeNull();
  });
});

describe('parseTrayParams', () => {
  it('trims title and tooltip, and accepts either alone', () => {
    expect(parseTrayParams({ title: ' 3 unread ', tooltip: ' Inbox ' })).toEqual({ title: '3 unread', tooltip: 'Inbox' });
    expect(parseTrayParams({ title: '3' })).toEqual({ title: '3', tooltip: '' });
    expect(parseTrayParams({ tooltip: 'Inbox' })).toEqual({ title: '', tooltip: 'Inbox' });
  });

  it.each([
    ['nothing', {}],
    ['only blanks', { title: '  ', tooltip: ' ' }],
    ['a title longer than the menu bar can show', { title: 'x'.repeat(25) }],
    ['a tooltip over the limit', { tooltip: 'x'.repeat(81) }],
    ['a non-string title', { title: 3 }],
    ['not an object', 'text'],
    ['an array', ['a']],
  ])('rejects %s', (_label, params) => {
    expect(parseTrayParams(params)).toBeNull();
  });

  it('accepts text exactly at the limits', () => {
    expect(parseTrayParams({ title: 'x'.repeat(24), tooltip: 'y'.repeat(80) })).not.toBeNull();
  });
});

describe('parseShortcutParams', () => {
  it('returns the canonical combination', () => {
    expect(parseShortcutParams({ accelerator: 'shift+commandorcontrol+k' })).toBe('CommandOrControl+Shift+K');
  });

  it.each([{}, { accelerator: 3 }, { accelerator: 'K' }, { accelerator: 'Ctrl+K' }, null, 'Alt+K'])('rejects %j', (params) => {
    expect(parseShortcutParams(params)).toBeNull();
  });
});

describe('parseFloatParams', () => {
  it('needs a real boolean', () => {
    expect(parseFloatParams({ on: true })).toBe(true);
    expect(parseFloatParams({ on: false })).toBe(false);
    for (const params of [{}, { on: 'true' }, { on: 1 }, null]) {
      expect(parseFloatParams(params)).toBeNull();
    }
  });
});

describe('capabilities', () => {
  it('has exactly the three desktop ones set apart from the two that work anywhere', () => {
    expect(BRIDGE_CAPABILITIES.filter(isDesktopCapability)).toEqual(['tray', 'shortcut', 'float']);
    expect(BRIDGE_CAPABILITIES.filter((capability) => !isDesktopCapability(capability))).toEqual(['notify', 'fetch']);
  });
});

describe('rpcEvent', () => {
  it('is a notification: no id, so a page cannot mistake it for the reply to a call', () => {
    const event = rpcEvent({ name: 'shortcut', data: { accelerator: 'Alt+F1' } });

    expect(event).toEqual({
      jsonrpc: '2.0',
      method: 'myrm/event',
      params: { name: 'shortcut', data: { accelerator: 'Alt+F1' } },
    });
    expect('id' in event).toBe(false);
    expect(parseRpcRequest(event)).toBeNull();
  });
});
