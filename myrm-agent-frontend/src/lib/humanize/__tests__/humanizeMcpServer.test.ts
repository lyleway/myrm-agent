/**
 * [INPUT]
 * - ../humanizeToolLine, ../humanizeApproval, ../humanizeProgressStep (real)
 * - locales/{en,zh}.json `humanize` namespace through next-intl's real translator
 *
 * [OUTPUT]
 * - Test suite proving a tool line names an MCP server the way its owner named it, and never by the internal id the
 *   model's tool name carries (`unnamed_dd4acd`)
 *
 * [POS]
 * The tool name only holds the server identity; the name the user typed comes from a resolver the caller injects. This
 * suite asserts the final sentence in both locales, so a missing or reworded key fails here.
 */

import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { AbstractIntlMessages } from 'next-intl';
import { describe, expect, it, vi } from 'vitest';

import type { ProgressItem } from '@/store/chat/types/progress';
import { humanizeApprovalTitle } from '../humanizeApproval';
import { humanizeProgressStep } from '../humanizeProgressStep';
import { humanizeToolLine } from '../humanizeToolLine';
import type { McpServerNameResolver, TranslateFn } from '../types';

// The global test setup replaces next-intl with an identity translator, which cannot tell a missing message from a present one.
const { createTranslator } = await vi.importActual<typeof import('next-intl')>('next-intl');

const LOCALES = ['en', 'zh'] as const;
type LocaleCode = (typeof LOCALES)[number];

const humanizeTranslator = (locale: LocaleCode): TranslateFn => {
  const messages = JSON.parse(
    readFileSync(join(process.cwd(), 'locales', `${locale}.json`), 'utf-8'),
  ) as AbstractIntlMessages;
  return createTranslator({ locale, messages, namespace: 'humanize', onError: () => undefined }) as TranslateFn;
};

/** What the user's settings know: the display name of each configured server, by the identity the runtime derives. */
const KNOWN: Record<string, string> = { unnamed_dd4acd: '天气', my_api: 'my-api', unnamed: 'unnamed' };
const resolve: McpServerNameResolver = (identity) => KNOWN[identity];

const FORECAST = 'mcp__unnamed_dd4acd__get_forecast';

const LINES: Record<LocaleCode, Record<'progress' | 'approval' | 'ask', string>> = {
  en: {
    progress: 'Use 天气 — get forecast',
    approval: 'Use 天气 — get forecast',
    ask: 'Wanted to use 天气 — get forecast',
  },
  zh: {
    progress: '使用 天气 — get forecast',
    approval: '使用 天气 — get forecast',
    ask: '想要使用 天气 — get forecast',
  },
};
const GENERIC: Record<LocaleCode, string> = {
  en: 'Use a connected service — get forecast',
  zh: '使用 已连接的服务 — get forecast',
};

describe.each(LOCALES)('an MCP tool line in %s', (locale) => {
  const t = humanizeTranslator(locale);

  it.each(['progress', 'approval', 'ask'] as const)(
    'names the server the way its owner named it, in %s tense',
    (mode) => {
      expect(humanizeToolLine(FORECAST, {}, t, mode, resolve)).toBe(LINES[locale][mode]);
    },
  );

  it('shows the name as typed for an ASCII server whose identity folds its separators', () => {
    expect(humanizeToolLine('mcp__my_api__list_items', {}, t, 'progress', resolve)).toBe(
      locale === 'en' ? 'Use my-api — list items' : '使用 my-api — list items',
    );
  });

  it('keeps a readable identity that nothing resolves', () => {
    const line = humanizeToolLine('mcp__github__search', {}, t, 'progress', resolve);

    expect(line).toBe(locale === 'en' ? 'Use github — search' : '使用 github — search');
    expect(humanizeToolLine('mcp__github__search', {}, t, 'progress')).toBe(line);
  });

  it('says "a connected service" for an identity no person can read, resolved by nobody', () => {
    expect(humanizeToolLine(FORECAST, {}, t, 'progress', () => undefined)).toBe(GENERIC[locale]);
    expect(humanizeToolLine(FORECAST, {}, t, 'progress')).toBe(GENERIC[locale]);
    expect(humanizeToolLine('mcp__unnamed__get_forecast', {}, t, 'progress')).toBe(GENERIC[locale]);
  });

  it('does not take a readable identity that only starts with "unnamed" for an opaque one', () => {
    expect(humanizeToolLine('mcp__unnamed_server__x', {}, t, 'progress')).toContain('unnamed_server');
    expect(humanizeToolLine('mcp__unnamed_dd4acd1__x', {}, t, 'progress')).toContain('unnamed_dd4acd1');
  });

  it('lets a resolved name win even when the identity looks opaque', () => {
    expect(humanizeToolLine('mcp__unnamed__x', {}, t, 'progress', resolve)).toContain('unnamed — x');
  });

  it.each([
    ['mcp__memory__create_entities', 'memory', 'create entities'],
    ['mcp__web_search__search', 'web_search', 'search'],
    ['mcp__slack__send_message', 'slack', 'send message'],
    ['mcp__ops__run_bash', 'ops', 'run bash'],
    ['mcp__docs__skill_lookup', 'docs', 'skill lookup'],
    ['mcp__fs__file_read', 'fs', 'file read'],
  ])('words %s as a tool of its server, not as the built-in tool that shares a word with it', (name, server, tool) => {
    const ctx = { query: 'cats', command: 'ls -la', filename: 'a.md', url: 'https://example.com', text: 'task' };

    expect(humanizeToolLine(name, ctx, t, 'approval')).toBe(
      locale === 'en' ? `Use ${server} — ${tool}` : `使用 ${server} — ${tool}`,
    );
  });

  it('never shows the internal id once the server is known', () => {
    for (const mode of ['progress', 'approval', 'ask'] as const) {
      expect(humanizeToolLine(FORECAST, {}, t, mode, resolve)).not.toContain('unnamed_dd4acd');
    }
  });

  it('passes the resolver through the approval title and the progress step title', () => {
    expect(humanizeApprovalTitle(FORECAST, {}, t, resolve)).toBe(LINES[locale].approval);
    expect(humanizeProgressStep({ tool_name: FORECAST } as ProgressItem, t, resolve)).toBe(LINES[locale].progress);
    expect(humanizeProgressStep({ tool_name: FORECAST, status: 'cancelled' } as ProgressItem, t, resolve)).toBe(
      LINES[locale].ask,
    );
  });
});

describe('a locale without the generic service label', () => {
  const english = humanizeTranslator('en');
  const noServiceLabel: TranslateFn = (key, values) => (key === 'fallback.service' ? key : english(key, values));

  it('falls back to the identity instead of showing a raw key', () => {
    expect(humanizeToolLine(FORECAST, {}, noServiceLabel, 'progress')).toBe('Use unnamed_dd4acd — get forecast');
  });
});
