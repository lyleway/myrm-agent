/**
 * The wording of the MoA overlay steps comes from the reason codes of the event: the lead-in says why the overlay ran,
 * the detail says why it stood down. The words are the `progressSteps` entries of the language of the moment; a code
 * without wording is still worded (lead-in) or shown as sent (detail), and a missing or non-text reason leaves the text
 * out. Before a translator is installed the English wording shows, never a key path.
 */
import { describe, expect, it, vi } from 'vitest';

import { installGlobalTranslator, type TestLocale } from '@/test-utils/globalTranslator';
import { formatMoaSkipReason, formatMoaTriggerReason } from '../statusStreamItemText';

const LEAD_INS: Record<TestLocale, [reason: string, leadIn: string][]> = {
  zh: [
    ['consecutive_tool_failures', '执行自适应协同 · '],
    ['loop_guard_warning', '循环优化协同 · '],
    ['multi_tool_failures', '多维诊断协同 · '],
    ['a_reason_without_wording', '协同决策 · '],
  ],
  en: [
    ['consecutive_tool_failures', 'Adaptive execution advisors · '],
    ['loop_guard_warning', 'Loop optimization advisors · '],
    ['multi_tool_failures', 'Multi-factor diagnosis advisors · '],
    ['a_reason_without_wording', 'Advisor decision · '],
  ],
};

const SKIP_REASONS: Record<TestLocale, [reason: string, text: string][]> = {
  zh: [
    ['risk_trigger_timeout', '协同超时回退'],
    ['budget_pressure', 'Token预算保护'],
    ['insufficient_refs', '参考模型未就绪'],
    ['a_reason_without_wording', 'a_reason_without_wording'],
  ],
  en: [
    ['risk_trigger_timeout', 'Advisors timed out, fell back'],
    ['budget_pressure', 'Token budget protection'],
    ['insufficient_refs', 'Reference models not ready'],
    ['a_reason_without_wording', 'a_reason_without_wording'],
  ],
};

describe.each(['zh', 'en'] as const)('MoA overlay wording in %s', (locale) => {
  describe('formatMoaTriggerReason', () => {
    it.each(LEAD_INS[locale])('words %s as the lead-in "%s"', async (reason, leadIn) => {
      await installGlobalTranslator(locale);
      expect(formatMoaTriggerReason(reason)).toBe(leadIn);
    });

    it.each([undefined, null, '', 42])('leaves the lead-in out for the reason %o', async (reason) => {
      await installGlobalTranslator(locale);
      expect(formatMoaTriggerReason(reason)).toBe('');
    });
  });

  describe('formatMoaSkipReason', () => {
    it.each(SKIP_REASONS[locale])('words %s as "%s"', async (reason, text) => {
      await installGlobalTranslator(locale);
      expect(formatMoaSkipReason(reason)).toBe(text);
    });

    it.each([undefined, null, '', 42])('leaves the detail out for the reason %o', async (reason) => {
      await installGlobalTranslator(locale);
      expect(formatMoaSkipReason(reason)).toBe('');
    });
  });
});

describe('before a translator is installed', () => {
  /** The translator lives in module state, so a fresh copy of the formatters is the one that has none. */
  async function untranslated() {
    vi.resetModules();
    return import('../statusStreamItemText');
  }

  it.each(LEAD_INS.en)('words %s with the English lead-in "%s"', async (reason, leadIn) => {
    const { formatMoaTriggerReason: lead } = await untranslated();
    expect(lead(reason)).toBe(leadIn);
  });

  it.each(SKIP_REASONS.en)('words %s with the English detail "%s"', async (reason, text) => {
    const { formatMoaSkipReason: detail } = await untranslated();
    expect(detail(reason)).toBe(text);
  });
});
