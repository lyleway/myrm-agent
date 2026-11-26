/**
 * [INPUT]
 * - harness STATUS `data` of `context_compaction` (POS: compaction progress; `elapsed_s` comes with compaction inside a
 *   turn, the pre-reply gate reports the phase alone)
 * - harness STATUS `data` of the MoA overlay steps (POS: `trigger_reason` / `reason` say why the overlay ran or stood
 *   down)
 * - @/services/i18nToastService::translateI18nKey (POS: Locale lookup outside React; the wording is resolved when the
 *   event is handled, in the language of that moment)
 *
 * [OUTPUT]
 * - formatCompactionItemText(): detail of a `context_compaction` step: the elapsed seconds while it runs or after a
 *   timeout, the tokens saved once it completes
 * - formatTokensSaved(): the tokens-saved detail shared by the compaction, pruning and archiving steps
 * - formatMoaTriggerReason() / formatMoaSkipReason(): lead-in and reason shown on the MoA overlay steps
 *
 * [POS]
 * Detail wording of the STATUS progress steps that is derived from the event payload. A figure the event does not carry
 * is left out, never filled in. Every word comes from the `progressSteps` locale namespace; the English fallbacks apply
 * only before the translator is installed.
 */

import { translateI18nKey } from '@/services/i18nToastService';

/** Tokens-saved detail of a step that shrank the context. */
export function formatTokensSaved(tokens: number): string {
  return `(${translateI18nKey('progressSteps.itemTokensSaved', `Tokens saved: ${tokens}`, { tokens })})`;
}

function moaTriggerLabel(reason: string): string {
  switch (reason) {
    case 'consecutive_tool_failures':
      return translateI18nKey('progressSteps.moaTriggerToolFailures', 'Adaptive execution advisors');
    case 'loop_guard_warning':
      return translateI18nKey('progressSteps.moaTriggerLoopGuard', 'Loop optimization advisors');
    case 'multi_tool_failures':
      return translateI18nKey('progressSteps.moaTriggerMultiToolFailures', 'Multi-factor diagnosis advisors');
    default:
      return translateI18nKey('progressSteps.moaTriggerDefault', 'Advisor decision');
  }
}

/** Lead-in of the MoA overlay step: why the overlay ran. */
export function formatMoaTriggerReason(reason: unknown): string {
  if (typeof reason !== 'string' || !reason) {
    return '';
  }
  return `${moaTriggerLabel(reason)} · `;
}

/** Reason shown when the MoA overlay stood down; a reason without wording is shown as sent. */
export function formatMoaSkipReason(reason: unknown): string {
  if (typeof reason !== 'string' || !reason) {
    return '';
  }
  switch (reason) {
    case 'risk_trigger_timeout':
      return translateI18nKey('progressSteps.moaSkipTimeout', 'Advisors timed out, fell back');
    case 'budget_pressure':
      return translateI18nKey('progressSteps.moaSkipBudgetPressure', 'Token budget protection');
    case 'insufficient_refs':
      return translateI18nKey('progressSteps.moaSkipInsufficientRefs', 'Reference models not ready');
    default:
      return reason;
  }
}

/** Detail of a `context_compaction` step; only a figure the event carries is shown. */
export function formatCompactionItemText(data: Record<string, unknown>): string {
  switch (data.phase) {
    case 'active':
    case 'timeout':
      return typeof data.elapsed_s === 'number' ? `(${data.elapsed_s}s)` : '';
    case 'completed':
      // The saved tokens are read from this payload; the pre-reply gate sends them on the event itself, so its step
      // has no detail.
      return typeof data.tokens_saved === 'number' && data.tokens_saved > 0 ? formatTokensSaved(data.tokens_saved) : '';
    default:
      return '';
  }
}
