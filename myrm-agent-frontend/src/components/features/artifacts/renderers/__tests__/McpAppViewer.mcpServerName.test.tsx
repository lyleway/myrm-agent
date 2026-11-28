/** @vitest-environment jsdom */
/**
 * [INPUT]
 * - ../McpAppViewer (real, with the real settings store and the real link-confirm store)
 * - locales/{en,zh}.json (real copy, through the real next-intl translator)
 *
 * [OUTPUT]
 * - Test suite proving the viewer names the server behind an MCP App by the name the user gave it, in the iframe title
 *   and in the desktop link prompt, and never by the internal identity of a server the settings do not know
 *
 * [POS]
 * The view carries the identity from the tool name (`unnamed_dd4acd` for `天气`). The prompt that asks whether to open a
 * link is a trust decision for the user, who knows the server only by the name they typed.
 */

import { act, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import * as tauriModule from '@/lib/tauri';
import type { McpAppView } from '@/store/chat/types';
import type { MCPServiceConfig } from '@/store/config/types';
import useConfigStore from '@/store/useConfigStore';
import { useExternalLinkConfirmStore } from '@/store/useExternalLinkConfirmStore';
import type { MockLocale } from '@/test-utils/realIntl';
import { McpAppViewer } from '../McpAppViewer';

const intl = vi.hoisted(() => ({ locale: 'en' as MockLocale }));
const { fetchWithTimeout } = vi.hoisted(() => ({ fetchWithTimeout: vi.fn() }));

vi.mock('next-intl', async () => (await import('@/test-utils/realIntl')).createRealIntlMock(() => intl.locale));
vi.mock('@/lib/api', () => ({ fetchWithTimeout }));

const connector = (name: string): MCPServiceConfig => ({
  name,
  type: 'streamable_http',
  url: 'https://example.com/mcp',
  description: '',
  enabled: true,
});

const viewOf = (serverName: string): McpAppView => ({ resourceUri: 'ui://weather/forecast', serverName });

/** Renders the viewer for the server and waits for the app iframe, whatever its title says. */
async function renderViewer(serverName: string): Promise<HTMLIFrameElement> {
  const { container } = render(<McpAppViewer view={viewOf(serverName)} />);
  await screen.findByTitle(/^MCP/);
  return container.querySelector('iframe') as HTMLIFrameElement;
}

function openLinkFrom(iframe: HTMLIFrameElement): void {
  act(() => {
    window.dispatchEvent(
      new MessageEvent('message', {
        data: { type: 'openLink', url: 'https://example.com/docs' },
        source: iframe.contentWindow,
      }),
    );
  });
}

beforeEach(() => {
  intl.locale = 'en';
  act(() => useConfigStore.setState({ mcpConfigs: [], orgMcpConfigs: [] }));
  useExternalLinkConfirmStore.setState({ open: false, link: null, declinedAt: 0 });
  fetchWithTimeout.mockResolvedValue({
    ok: true,
    json: async () => ({ data: { content: btoa('<p>app</p>') } }),
  });
  vi.spyOn(tauriModule, 'isTauriEnvironment').mockReturnValue(true);
});

afterEach(() => {
  vi.restoreAllMocks();
  fetchWithTimeout.mockReset();
});

describe('McpAppViewer server names', () => {
  it.each([
    ['en', 'MCP App: 天气'],
    ['zh', 'MCP 应用: 天气'],
  ] as const)('titles the app with the name the user gave its server, in %s', async (locale, title) => {
    intl.locale = locale;
    act(() => useConfigStore.setState({ mcpConfigs: [connector('天气')] }));

    const iframe = await renderViewer('unnamed_dd4acd');

    expect(iframe).toHaveAttribute('title', title);
  });

  it.each([
    ['en', 'MCP App'],
    ['zh', 'MCP 应用'],
  ] as const)('never shows the internal id of a server the settings do not know, in %s', async (locale, title) => {
    intl.locale = locale;

    const iframe = await renderViewer('unnamed_dd4acd');

    expect(iframe).toHaveAttribute('title', title);
  });

  it('shows an identity a person can read when the settings do not know the server', async () => {
    const iframe = await renderViewer('Linear');

    expect(iframe).toHaveAttribute('title', 'MCP App: Linear');
  });

  it('asks for the resource of the server by its identity, which the backend resolves', async () => {
    act(() => useConfigStore.setState({ mcpConfigs: [connector('天气')] }));

    await renderViewer('unnamed_dd4acd');

    const [url] = fetchWithTimeout.mock.calls[0] as [string];
    expect(new URLSearchParams(url.split('?')[1]).get('server')).toBe('unnamed_dd4acd');
  });

  it('asks the user about a link naming the server by the name the user gave it', async () => {
    act(() => useConfigStore.setState({ mcpConfigs: [connector('天气')] }));
    const iframe = await renderViewer('unnamed_dd4acd');

    openLinkFrom(iframe);

    expect(useExternalLinkConfirmStore.getState().link).toEqual({ url: 'https://example.com/docs', source: '天气' });
  });

  it('asks the user about a link without a source when the settings do not know the server', async () => {
    const iframe = await renderViewer('unnamed_dd4acd');

    openLinkFrom(iframe);

    expect(useExternalLinkConfirmStore.getState().link).toEqual({ url: 'https://example.com/docs', source: null });
  });

  it('picks the name up, without reloading the app, when the settings load after it is shown', async () => {
    const iframe = await renderViewer('unnamed_dd4acd');
    expect(iframe).toHaveAttribute('title', 'MCP App');

    act(() => useConfigStore.setState({ mcpConfigs: [connector('天气')] }));
    openLinkFrom(iframe);

    expect(iframe).toHaveAttribute('title', 'MCP App: 天气');
    expect(useExternalLinkConfirmStore.getState().link?.source).toBe('天气');
    expect(fetchWithTimeout).toHaveBeenCalledTimes(1);
  });
});
