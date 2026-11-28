/** @vitest-environment jsdom */
/**
 * [INPUT]
 * - ../CronRunBlockedActions (real, with the real next-intl translator of the `cron` and `humanize` namespaces)
 * - locales/{zh,en}.json (real copy)
 *
 * [OUTPUT]
 * - Test suite proving the card names every stopped action in plain words, pluralises its title and counts the
 *   actions its list leaves out, in every locale
 *
 * [POS]
 * The tool lines come from the humanize SSOT, which resolves its keys at runtime, so the static i18n gate cannot see
 * them. A missing key renders as "humanize.<key>" there, and the stub translator of the item suite hides that; this
 * suite fails on it.
 */

import { act, render, screen } from '@testing-library/react';
import { NextIntlClientProvider } from 'next-intl';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CronBlockedAction } from '@/services/cron';
import type { MCPServiceConfig } from '@/store/config/types';
import useConfigStore from '@/store/useConfigStore';
import CronRunBlockedActions from '../CronRunBlockedActions';
import en from '../../../../../locales/en.json';
import zh from '../../../../../locales/zh.json';

// The global setup stubs next-intl with an identity translator; this suite asserts the real locale copy.
vi.mock('next-intl', async (importOriginal) => importOriginal<typeof import('next-intl')>());

const LOCALES = { en, zh } as const;
type LocaleCode = keyof typeof LOCALES;

const ACTIONS: CronBlockedAction[] = [
  { tool: 'file_write_tool', reason: 'Capability not granted: file_write', count: 2 },
  { tool: 'bash_code_execute_tool', reason: 'cron fail-closed: no explicit capability declaration', count: 1 },
  { tool: 'spawn_agent_tool', reason: 'Capability not granted: sub_agent', count: 1 },
];
const LISTED_COUNT = 4;

const renderCard = (locale: LocaleCode, count: number, actions: CronBlockedAction[] = ACTIONS) =>
  render(
    <NextIntlClientProvider
      locale={locale}
      messages={{ cron: LOCALES[locale].cron, humanize: LOCALES[locale].humanize }}
    >
      <CronRunBlockedActions count={count} actions={actions} />
    </NextIntlClientProvider>,
  );

describe('CronRunBlockedActions', () => {
  it('names each stopped action in plain words with its reason and repeat count', () => {
    renderCard('en', LISTED_COUNT);

    expect(screen.getByText('Safety rules stopped 4 actions')).toBeInTheDocument();
    expect(screen.getByText('Wanted to write a file')).toBeInTheDocument();
    expect(screen.getByText('×2')).toBeInTheDocument();
    expect(screen.getByText('Capability not granted: file_write')).toBeInTheDocument();
    expect(screen.getByText('Wanted to run a command')).toBeInTheDocument();
    expect(screen.getByText('cron fail-closed: no explicit capability declaration')).toBeInTheDocument();
    expect(screen.queryByText('×1')).toBeNull();
  });

  it('points to the job settings that can allow a step and names what an unattended run still stops', () => {
    renderCard('en', LISTED_COUNT);

    expect(
      screen.getByText(
        "Steps outside this job's Capability Fence or Allowed Paths can be allowed in the job settings. " +
          'Whatever you allow, an unattended run still stops steps that touch protected files, and steps that ' +
          'look like payments, Google Workspace writes, or sending data out after a credential file was read ' +
          "(opening web pages on your Domain Allowlist doesn't count).",
      ),
    ).toBeInTheDocument();
  });

  it('uses the singular title for a single action', () => {
    renderCard('en', 1, [ACTIONS[0]]);

    expect(screen.getByText('Safety rules stopped 1 action')).toBeInTheDocument();
  });

  it('counts the actions the list leaves out', () => {
    renderCard('en', LISTED_COUNT + 12);

    expect(screen.getByText('Safety rules stopped 16 actions')).toBeInTheDocument();
    expect(screen.getByText('and 12 more')).toBeInTheDocument();
  });

  it('shows no "more" line when the list is complete', () => {
    renderCard('en', LISTED_COUNT);

    expect(screen.queryByText(/and \d+ more/)).toBeNull();
  });

  it('keeps the title and hint when the run recorded a count but no groups', () => {
    renderCard('en', 3, []);

    expect(screen.getByText('Safety rules stopped 3 actions')).toBeInTheDocument();
    expect(screen.queryByRole('list')).toBeNull();
  });

  it('describes a tool the humanize rules do not know as something it wanted, never as used', () => {
    renderCard('en', 2, [
      { tool: 'spawn_agent_tool', reason: 'Capability not granted: sub_agent', count: 1 },
      { tool: 'ssrf', reason: 'Request to a private address', count: 1 },
    ]);

    expect(screen.getByText('Wanted to use Spawn Agent')).toBeInTheDocument();
    expect(screen.getByText('Wanted to use Ssrf')).toBeInTheDocument();
    expect(screen.queryByText(/^Used /)).toBeNull();
  });

  it.each(Object.keys(LOCALES) as LocaleCode[])('renders translated copy without raw key paths in %s', (locale) => {
    const { container } = renderCard(locale, LISTED_COUNT + 12);

    const text = container.textContent ?? '';
    expect(text).not.toMatch(/\b(cron|humanize)\.\w+/);
    expect(text).toContain('16');
    expect(text).toContain('12');
    expect(LOCALES[locale].humanize.ask).toHaveProperty('tool_generic');
  });
});

describe('CronRunBlockedActions connector names', () => {
  const FORECAST: CronBlockedAction = {
    tool: 'mcp__unnamed_dd4acd__get_forecast',
    reason: 'Capability not granted: mcp',
    count: 1,
  };
  const configured = (name: string): MCPServiceConfig => ({
    name,
    type: 'streamable_http',
    url: 'https://example.com/mcp',
    description: '',
    enabled: true,
  });

  beforeEach(() => {
    act(() => useConfigStore.setState({ mcpConfigs: [], orgMcpConfigs: [] }));
  });

  it('names a stopped connector action by the name the user gave the connector', () => {
    act(() => useConfigStore.setState({ mcpConfigs: [configured('天气')] }));

    renderCard('en', 1, [FORECAST]);
    expect(screen.getByText('Wanted to use 天气 — get forecast')).toBeInTheDocument();
  });

  it.each([
    ['en', 'Wanted to use a connected service — get forecast'],
    ['zh', '想要使用 已连接的服务 — get forecast'],
  ] as const)('never shows the internal id of a connector the settings do not know, in %s', (locale, line) => {
    const { container } = renderCard(locale, 1, [FORECAST]);

    expect(screen.getByText(line)).toBeInTheDocument();
    expect(container.textContent).not.toContain('unnamed');
  });
});
