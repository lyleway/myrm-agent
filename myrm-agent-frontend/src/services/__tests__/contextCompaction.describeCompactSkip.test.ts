import { describe, expect, it } from 'vitest';
import { describeCompactSkip } from '@/services/contextCompaction';
import en from '../../../locales/en.json';
import zh from '../../../locales/zh.json';

/** Every reason the compaction service reports for a request it did not run. */
const SKIP_CASES = [
  { reason: 'no_compactable_messages', messageKey: 'chat.compact.skip.nothing', severity: 'info' },
  { reason: 'too_few_messages', messageKey: 'chat.compact.skip.nothing', severity: 'info' },
  // A new chat has an id before its first message is sent; the server has no such chat yet, and it holds nothing.
  { reason: 'chat_not_found', messageKey: 'chat.compact.skip.nothing', severity: 'info' },
  { reason: 'concurrent_compaction_in_progress', messageKey: 'chat.compact.skip.busy', severity: 'info' },
  { reason: 'summarize_circuit_open', messageKey: 'chat.compact.skip.paused', severity: 'warning' },
  { reason: 'compression_anti_thrash_active', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'timeout', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'persist_failed', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'too_few_messages (2 < 4)', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'a_reason_added_later', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: 'constructor', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: '__proto__', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: '', messageKey: 'chat.compact.failed', severity: 'error' },
  { reason: null, messageKey: 'chat.compact.failed', severity: 'error' },
] as const;

/** The text a dotted key resolves to in a locale bundle, or undefined when the bundle has none. */
function copyOf(bundle: unknown, key: string): unknown {
  return key
    .split('.')
    .reduce<unknown>(
      (node, part) => (typeof node === 'object' && node !== null ? (node as Record<string, unknown>)[part] : undefined),
      bundle,
    );
}

describe('describeCompactSkip', () => {
  it.each(SKIP_CASES)('words $reason as $messageKey ($severity)', ({ reason, messageKey, severity }) => {
    expect(describeCompactSkip(reason)).toEqual({ messageKey, severity });
  });

  it.each([
    { locale: 'English', bundle: en },
    { locale: 'Chinese', bundle: zh },
  ])('has $locale copy for every notice', ({ bundle }) => {
    const keys = new Set(SKIP_CASES.map(({ reason }) => describeCompactSkip(reason).messageKey));

    expect([...keys].filter((key) => typeof copyOf(bundle, key) !== 'string')).toEqual([]);
  });
});
