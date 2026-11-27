import { describe, expect, it } from 'vitest';
import type { CapabilityStatus } from '../declaration';
import { AI_OUTPUT_TOKENS_DEFAULT, AI_OUTPUT_TOKENS_MAX, AI_PROMPT_MAX, AI_SYSTEM_MAX } from '../protocol';
import { aiReply, reasonOf, request, setup, SHA, surfaceCalls } from './hostHarness';

describe('ai.complete', () => {
  it('forwards the prompt with the version and hands back only the text', async () => {
    const { handle, proxyAi, activity } = setup();
    proxyAi.mockResolvedValue(aiReply('Sunny'));

    const reply = await handle(request('ai.complete', { prompt: 'Weather?', system: 'Be brief', maxTokens: 200 }));

    expect(proxyAi).toHaveBeenCalledWith({
      prompt: 'Weather?',
      system: 'Be brief',
      maxTokens: 200,
      version_sha256: SHA,
    });
    expect(reply).toEqual({ jsonrpc: '2.0', id: 1, result: { text: 'Sunny' } });
    // The server logs what it proxies; the host adds nothing, and never the prompt.
    expect(activity).toEqual([]);
  });

  it('applies the default output cap and no system text when the page names none', async () => {
    const { handle, proxyAi } = setup();
    proxyAi.mockResolvedValue(aiReply());

    await handle(request('ai.complete', { prompt: 'Hi' }));

    expect(proxyAi).toHaveBeenCalledWith({
      prompt: 'Hi',
      system: null,
      maxTokens: AI_OUTPUT_TOKENS_DEFAULT,
      version_sha256: SHA,
    });
  });

  it.each<[CapabilityStatus, string]>([
    ['ask', 'permission_denied'],
    ['denied', 'permission_denied'],
    ['undeclared', 'not_declared'],
  ])('refuses before the server while the capability is %s, logging no text', async (status, reason) => {
    const { handle, proxyAi, activity } = setup({ ai: status });

    const reply = await handle(request('ai.complete', { prompt: 'my secret diary' }));

    expect(reasonOf(reply)).toBe(reason);
    expect(proxyAi).not.toHaveBeenCalled();
    expect(activity).toEqual([{ capability: 'ai', outcome: 'denied', detail: null }]);
  });

  it.each([
    ['no prompt', {}],
    ['a prompt that is not text', { prompt: 3 }],
    ['a blank prompt', { prompt: '   ' }],
    ['an oversize prompt', { prompt: 'x'.repeat(AI_PROMPT_MAX + 1) }],
    ['an oversize system text', { prompt: 'hi', system: 'x'.repeat(AI_SYSTEM_MAX + 1) }],
    ['a system text that is not text', { prompt: 'hi', system: 3 }],
    ['a zero output cap', { prompt: 'hi', maxTokens: 0 }],
    ['a fractional output cap', { prompt: 'hi', maxTokens: 1.5 }],
    ['an output cap over the limit', { prompt: 'hi', maxTokens: AI_OUTPUT_TOKENS_MAX + 1 }],
    ['an output cap that is not a number', { prompt: 'hi', maxTokens: '100' }],
  ])('rejects %s once the capability is allowed', async (_name, params) => {
    const { handle, proxyAi } = setup();

    expect(reasonOf(await handle(request('ai.complete', params)))).toBe('invalid_params');
    expect(proxyAi).not.toHaveBeenCalled();
  });

  it.each([
    ['permission_denied', 'permission_denied'],
    ['rate_limited', 'rate_limited'],
    ['budget_exceeded', 'budget_exceeded'],
    ['unavailable', 'unavailable'],
    ['timeout', 'timeout'],
    ['upstream_error', 'upstream_error'],
    ['something_new', 'failed'],
  ])('maps the server refusal %s to %s', async (code, reason) => {
    const { handle, proxyAi } = setup();
    proxyAi.mockResolvedValue({ ok: false, text: '', code, message: 'refused' });

    const reply = await handle(request('ai.complete', { prompt: 'hi' }));

    expect(reasonOf(reply)).toBe(reason);
    expect(reply).toMatchObject({ error: { message: 'refused' } });
  });

  it('reports a request that never reached the server as failed without leaking the cause', async () => {
    const { handle, proxyAi } = setup();
    proxyAi.mockRejectedValue(new Error('connect ECONNREFUSED 127.0.0.1:8000 token=abc'));

    const reply = await handle(request('ai.complete', { prompt: 'hi' }));

    expect(reasonOf(reply)).toBe('failed');
    expect(JSON.stringify(reply)).not.toContain('ECONNREFUSED');
  });

  it('bounds denied-call chatter at 40 a minute and logs the first refusal once', async () => {
    const { handle, activity } = setup({ ai: 'denied' });
    for (let index = 0; index < 60; index += 1) {
      await handle(request('ai.complete', { prompt: 'hi' }, index));
    }

    expect(activity.filter((entry) => entry.outcome === 'denied')).toHaveLength(40);
    expect(activity.filter((entry) => entry.outcome === 'rate_limited')).toHaveLength(1);
  });

  it('keeps its own budget apart from fetch', async () => {
    const { handle, proxyAi } = setup();
    proxyAi.mockResolvedValue(aiReply());
    for (let index = 0; index < 120; index += 1) {
      await handle(request('fetch', { url: 'not a url' }, index));
    }

    expect(await handle(request('ai.complete', { prompt: 'hi' }, 500))).toMatchObject({ result: { text: 'Sunny' } });
  });

  it('works outside the desktop window and leaves nothing on the desktop to take down', async () => {
    const { handle, host, proxyAi, surface } = setup({}, { desktop: false });
    proxyAi.mockResolvedValue(aiReply());

    expect(await handle(request('ai.complete', { prompt: 'hi' }))).toMatchObject({ result: { text: 'Sunny' } });
    await host.revoke('ai');
    await host.dispose();

    expect(surfaceCalls(surface)).toEqual([]);
  });
});
