/**
 * A chat that went quiet for hours is compacted before the model answers, so the compaction events are the first of
 * the turn: the user's message is stored and no reply exists yet. They must still open the reply's progress step, or
 * the page shows nothing of a compaction that took seconds.
 */
import { describe, expect, it } from 'vitest';

import { expectDefined } from '@/test-utils/expectDefined';
import { statusStreamBeforeReply } from './statusStepTestKit';

describe('context_compaction before the reply exists', () => {
  it('opens the reply of the turn with the running step', async () => {
    const stream = statusStreamBeforeReply();

    await stream.apply('context_compaction', { phase: 'active' }, { status: 'active' });

    const reply = stream.reply();
    expectDefined(reply, 'reply opened by the compaction');
    expect(reply.chatId).toBe('chat-1');
    expect(reply.progressSteps).toMatchObject([{ step_key: 'context_compaction', status: 'active', items: [] }]);
    expect(stream.messages.filter((message) => message.role === 'assistant')).toHaveLength(1);
  });

  it.each([
    { phase: 'completed', sent: 'success', shown: 'complete' },
    { phase: 'ineffective', sent: 'success', shown: 'success' },
    { phase: 'skipped', sent: 'success', shown: 'success' },
    { phase: 'failed', sent: 'warning', shown: 'warning' },
  ])('closes the same step on the $phase phase instead of adding a second', async ({ phase, sent, shown }) => {
    const stream = statusStreamBeforeReply();

    await stream.apply('context_compaction', { phase: 'active' }, { status: 'active' });
    await stream.apply('context_compaction', { phase }, { status: sent });

    expect(stream.reply()?.progressSteps).toMatchObject([{ step_key: `context_compaction_${phase}`, status: shown }]);
  });
});
