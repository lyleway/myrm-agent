/**
 * The MoA overlay steps word their detail from the reason codes of the event: the step that opens says why the overlay
 * ran and which reference models it consults, the step that closes early says why it stood down. The wording itself
 * lives in `statusStreamItemText.ts`; this pins that the handler hands it the right fields.
 */
import { describe, expect, it, vi } from 'vitest';

import { applyStatusEvent } from './statusStepTestKit';

// The early close also raises a toast; the handler loads the toast service on demand.
vi.mock('@/services/i18nToastService', () => ({ showI18nToast: vi.fn() }));

describe('moa_overlay_active', () => {
  it.each([
    { trigger_reason: 'loop_guard_warning', text: '循环优化协同 · (model-a, model-b)' },
    { trigger_reason: 'a_reason_without_wording', text: '协同决策 · (model-a, model-b)' },
    { trigger_reason: undefined, text: '(model-a, model-b)' },
  ])('leads with the reason $trigger_reason gave and lists the reference models', async ({ trigger_reason, text }) => {
    const message = await applyStatusEvent('moa_overlay_active', {
      trigger_reason,
      reference_models: ['model-a', 'model-b'],
    });

    expect(message.progressSteps?.[0].items).toEqual([{ text }]);
    expect(message.consensusRefsExpected).toBe(2);
  });
});

describe('moa_overlay_skipped', () => {
  it.each([
    { reason: 'budget_pressure', text: '(Token预算保护)' },
    { reason: 'a_reason_without_wording', text: '(a_reason_without_wording)' },
  ])('says the overlay stood down for $reason', async ({ reason, text }) => {
    const message = await applyStatusEvent('moa_overlay_skipped', { reason });

    expect(message.progressSteps?.[0].items).toEqual([{ text }]);
  });
});
