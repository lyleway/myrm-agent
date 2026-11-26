/**
 * The detail text of a `context_compaction` step comes from what the event carries: the elapsed seconds while
 * compaction runs inside a turn, the tokens saved once it completes. An event that carries no figure leaves the step
 * without a detail instead of showing one the stream never sent. The pruning and archiving steps word their saving the
 * same way.
 */
import { beforeEach, describe, expect, it } from 'vitest';

import type { ProgressItem } from '@/store/chat/types';
import { installGlobalTranslator } from '@/test-utils/globalTranslator';
import { applyStatusEvent } from './statusStepTestKit';

/** Feed the handler one `context_compaction` event and return the detail items of the step it produced. */
async function detailOf({
  data,
  ...envelope
}: {
  data: Record<string, unknown>;
  status?: string;
}): Promise<ProgressItem['items']> {
  const message = await applyStatusEvent('context_compaction', data, envelope);
  return message.progressSteps?.[0].items;
}

describe('context_compaction detail text', () => {
  describe('elapsed seconds', () => {
    it.each([
      { phase: 'active', seconds: 10, text: '(10s)' },
      { phase: 'active', seconds: 20.3, text: '(20.3s)' },
      { phase: 'timeout', seconds: 90, text: '(90s)' },
    ])('shows $text for the $phase phase when the event reports $seconds seconds', async ({ phase, seconds, text }) => {
      expect(await detailOf({ data: { phase, elapsed_s: seconds } })).toEqual([{ text }]);
    });

    it.each(['active', 'timeout'])('shows no time for the %s phase when the event reports none', async (phase) => {
      expect(await detailOf({ data: { phase } })).toEqual([]);
    });

    it('opens the pre-reply gate step without a time, since the gate does not time its compaction', async () => {
      expect(await detailOf({ status: 'active', data: { phase: 'active' } })).toEqual([]);
    });
  });

  describe('tokens saved', () => {
    beforeEach(async () => {
      await installGlobalTranslator('en');
    });

    it('shows what a completed event reports in its payload', async () => {
      expect(await detailOf({ data: { phase: 'completed', tokens_saved: 4200 } })).toEqual([
        { text: '(Tokens saved: 4200)' },
      ]);
    });

    it('words it in the language of the moment', async () => {
      await installGlobalTranslator('zh');

      expect(await detailOf({ data: { phase: 'completed', tokens_saved: 4200 } })).toEqual([
        { text: '(节省 token：4200)' },
      ]);
    });

    it.each([{}, { tokens_saved: 0 }])(
      'shows nothing for a completed payload that saved no tokens: %o',
      async (saved) => {
        expect(await detailOf({ data: { phase: 'completed', ...saved } })).toEqual([]);
      },
    );
  });

  describe('tokens saved by pruning and archiving', () => {
    beforeEach(async () => {
      await installGlobalTranslator('zh');
    });

    it.each(['context_pruned', 'memory_archived'])(
      'shows what the %s event reports, in the language of the moment',
      async (stepKey) => {
        const message = await applyStatusEvent(stepKey, {}, { tokens_saved: 900 });

        expect(message.progressSteps?.[0].items).toEqual([{ text: '(节省 token：900)' }]);
      },
    );

    it.each(['context_pruned', 'memory_archived'])(
      'shows no detail when the %s event reports no saving',
      async (stepKey) => {
        const message = await applyStatusEvent(stepKey, {}, { tokens_saved: 0 });

        expect(message.progressSteps?.[0].items).toEqual([]);
      },
    );
  });

  it.each(['circuit_open', 'fallback', 'ineffective', 'skipped', 'failed'])(
    'shows no detail for the %s phase',
    async (phase) => {
      expect(await detailOf({ data: { phase } })).toEqual([]);
    },
  );
});
