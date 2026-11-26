/**
 * The tokens-saved detail of the compaction, pruning and archiving steps is a `progressSteps` entry that takes the figure
 * as its placeholder, so each language puts the number where its own wording wants it. The figure is shown as the event
 * sent it, without digit grouping. Before a translator is installed the English wording shows.
 */
import { describe, expect, it, vi } from 'vitest';

import { installGlobalTranslator } from '@/test-utils/globalTranslator';
import { formatTokensSaved } from '../statusStreamItemText';

describe('formatTokensSaved', () => {
  it.each([
    { locale: 'zh', tokens: 4200, text: '(节省 token：4200)' },
    { locale: 'en', tokens: 4200, text: '(Tokens saved: 4200)' },
    { locale: 'en', tokens: 1234567, text: '(Tokens saved: 1234567)' },
  ] as const)('words $tokens tokens in $locale as $text', async ({ locale, tokens, text }) => {
    await installGlobalTranslator(locale);
    expect(formatTokensSaved(tokens)).toBe(text);
  });

  it('words the figure in English before a translator is installed', async () => {
    // The translator lives in module state, so a fresh copy of the formatter is the one that has none.
    vi.resetModules();
    const { formatTokensSaved: untranslated } = await import('../statusStreamItemText');

    expect(untranslated(4200)).toBe('(Tokens saved: 4200)');
  });
});
