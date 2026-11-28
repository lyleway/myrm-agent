/** @vitest-environment jsdom */
/**
 * [INPUT]
 * - ../ProgressSteps (real, with the real settings store)
 *
 * [OUTPUT]
 * - Test suite proving the step list titles an MCP tool call with the name the user gave the server, and with a generic
 *   label (never the internal id) for a server the settings do not know
 *
 * [POS]
 * The tool name the model called carries only the server identity (`unnamed_dd4acd`). The names live in the settings
 * store, which the shell reads once and hands down to every step title.
 */

import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

// Echoes the key and the values it was given, except for the one generic label, which the humanize layer needs as text.
const echoT = (key: string, values?: Record<string, string | number>): string => {
  if (key === 'fallback.service') {
    return 'a connected service';
  }
  return values === undefined ? key : `${key}:${JSON.stringify(values)}`;
};

vi.mock('next-intl', () => ({
  useTranslations: () => echoT,
  useLocale: () => 'en',
}));

vi.mock('@/store/useChatStore', () => ({
  default: (selector: (state: unknown) => unknown) => selector({ sendMessage: vi.fn(), messages: [] }),
}));

import ProgressSteps from '../ProgressSteps';
import type { ProgressItem } from '@/store/chat/types';
import type { MCPServiceConfig } from '@/store/config/types';
import useConfigStore from '@/store/useConfigStore';

const server = (name: string): MCPServiceConfig => ({
  name,
  type: 'streamable_http',
  url: 'https://example.com/mcp',
  description: '',
  enabled: true,
});

const toolStep = (tool: string): ProgressItem => ({ step_key: tool, tool_name: tool, status: 'success' });

const open = async (steps: ProgressItem[]) => {
  render(<ProgressSteps messageId="m-1" steps={steps} loading={false} />);
  await userEvent.click(screen.getByTestId('progress-steps-toggle'));
};

beforeEach(() => {
  act(() => useConfigStore.setState({ mcpConfigs: [], orgMcpConfigs: [] }));
});

describe('ProgressSteps MCP server names', () => {
  it('titles an MCP tool call with the name the user gave the server', async () => {
    act(() => useConfigStore.setState({ mcpConfigs: [server('天气')] }));

    await open([toolStep('mcp__unnamed_dd4acd__get_forecast')]);

    expect(screen.getByRole('heading', { level: 4 })).toHaveTextContent(
      'progress.mcp:{"server":"天气","tool":"get forecast"}',
    );
  });

  it('follows a server that is configured after the step was drawn', async () => {
    await open([toolStep('mcp__unnamed_dd4acd__get_forecast')]);
    expect(screen.getByRole('heading', { level: 4 })).toHaveTextContent('"server":"a connected service"');

    act(() => useConfigStore.setState({ mcpConfigs: [server('天气')] }));

    expect(screen.getByRole('heading', { level: 4 })).toHaveTextContent('"server":"天气"');
  });

  it('words a server the settings do not know by a generic label, never by its internal id', async () => {
    await open([toolStep('mcp__unnamed_13bd86__get_forecast')]);

    const title = screen.getByRole('heading', { level: 4 });
    expect(title).toHaveTextContent('"server":"a connected service"');
    expect(title.textContent).not.toContain('unnamed');
  });
});
