/**
 * The MoA overlay steps word their detail from the reason codes of the event: the step that opens says why the overlay
 * ran and which reference models it consults, the step that closes early says why it stood down. The wording itself
 * lives in `statusStreamItemText.ts`; this pins that the handler hands it the right fields and that the words come in
 * the language of the moment.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { installGlobalTranslator } from '@/test-utils/globalTranslator';
import { applyStatusEvent } from './statusStepTestKit';

// The early close also raises a toast; the handler loads the toast service on demand. Only the toast is replaced: the
// wording of the steps goes through the real `translateI18nKey`.
vi.mock('@/services/i18nToastService', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/i18nToastService')>()),
  showI18nToast: vi.fn(),
}));

beforeEach(async () => {
  await installGlobalTranslator('en');
});

describe('moa_overlay_active', () => {
  it.each([
    { trigger_reason: 'loop_guard_warning', text: 'Loop optimization advisors · (model-a, model-b)' },
    { trigger_reason: 'a_reason_without_wording', text: 'Advisor decision · (model-a, model-b)' },
    { trigger_reason: undefined, text: '(model-a, model-b)' },
  ])('leads with the reason $trigger_reason gave and lists the reference models', async ({ trigger_reason, text }) => {
    const message = await applyStatusEvent('moa_overlay_active', {
      trigger_reason,
      reference_models: ['model-a', 'model-b'],
    });

    expect(message.progressSteps?.[0].items).toEqual([{ text }]);
    expect(message.consensusRefsExpected).toBe(2);
  });

  it('leads in the language of the moment', async () => {
    await installGlobalTranslator('zh');

    const message = await applyStatusEvent('moa_overlay_active', {
      trigger_reason: 'loop_guard_warning',
      reference_models: ['model-a', 'model-b'],
    });

    expect(message.progressSteps?.[0].items).toEqual([{ text: '循环优化协同 · (model-a, model-b)' }]);
  });
});

describe('moa_overlay_skipped', () => {
  it.each([
    { reason: 'budget_pressure', text: '(Token budget protection)' },
    { reason: 'a_reason_without_wording', text: '(a_reason_without_wording)' },
  ])('says the overlay stood down for $reason', async ({ reason, text }) => {
    const message = await applyStatusEvent('moa_overlay_skipped', { reason });

    expect(message.progressSteps?.[0].items).toEqual([{ text }]);
  });

  it('says it in the language of the moment', async () => {
    await installGlobalTranslator('zh');

    const message = await applyStatusEvent('moa_overlay_skipped', { reason: 'budget_pressure' });

    expect(message.progressSteps?.[0].items).toEqual([{ text: '(Token预算保护)' }]);
  });
});
