/**
 * The wording of the MoA overlay steps comes from the reason codes of the event: the lead-in says why the overlay ran,
 * the detail says why it stood down. A code without wording is still worded (lead-in) or shown as sent (detail), and a
 * missing or non-text reason leaves the text out.
 */
import { describe, expect, it } from 'vitest';

import { formatMoaSkipReason, formatMoaTriggerReason } from '../statusStreamItemText';

describe('formatMoaTriggerReason', () => {
  it.each([
    ['consecutive_tool_failures', '执行自适应协同 · '],
    ['loop_guard_warning', '循环优化协同 · '],
    ['multi_tool_failures', '多维诊断协同 · '],
    ['a_reason_without_wording', '协同决策 · '],
  ])('words %s as the lead-in "%s"', (reason, leadIn) => {
    expect(formatMoaTriggerReason(reason)).toBe(leadIn);
  });

  it.each([undefined, null, '', 42])('leaves the lead-in out for the reason %o', (reason) => {
    expect(formatMoaTriggerReason(reason)).toBe('');
  });
});

describe('formatMoaSkipReason', () => {
  it.each([
    ['risk_trigger_timeout', '协同超时回退'],
    ['budget_pressure', 'Token预算保护'],
    ['insufficient_refs', '参考模型未就绪'],
    ['a_reason_without_wording', 'a_reason_without_wording'],
  ])('words %s as "%s"', (reason, text) => {
    expect(formatMoaSkipReason(reason)).toBe(text);
  });

  it.each([undefined, null, '', 42])('leaves the detail out for the reason %o', (reason) => {
    expect(formatMoaSkipReason(reason)).toBe('');
  });
});
