/** @vitest-environment jsdom */
/**
 * [INPUT]
 * - ../ApprovalDetails (real, with the real settings store)
 * - locales/{en,zh}.json (real copy, through the real next-intl translator)
 *
 * [OUTPUT]
 * - Test suite proving the approval card titles an MCP tool call with the name the user gave the connector, and with a
 *   generic label (never the internal id) for a connector the settings do not know
 *
 * [POS]
 * The question "may this run?" is the one place a user must recognise what is asked. The tool name the model called
 * carries only the connector's internal identity; the card words it from the settings.
 */

import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ToolApprovalRequest } from '@/store/chat/types';
import type { MCPServiceConfig } from '@/store/config/types';
import useConfigStore from '@/store/useConfigStore';
import type { MockLocale } from '@/test-utils/realIntl';
import ApprovalDetails from '../ApprovalDetails';

const intl = vi.hoisted(() => ({ locale: 'en' as MockLocale }));

vi.mock('next-intl', async () => (await import('@/test-utils/realIntl')).createRealIntlMock(() => intl.locale));

const connector = (name: string): MCPServiceConfig => ({
  name,
  type: 'streamable_http',
  url: 'https://example.com/mcp',
  description: '',
  enabled: true,
});

const request = (toolName: string): ToolApprovalRequest => ({
  requestId: 'req-mcp',
  toolName,
  toolInput: {},
  reason: 'Needs your approval',
  timeoutSeconds: 60,
  expiresAt: Math.floor(Date.now() / 1000) + 60,
  timeoutBehavior: 'deny',
  messageId: 'msg-1',
  displayMode: 'approval',
  chatId: 'chat-1',
  actionMode: 'agent',
});

const renderDetails = (toolName: string) =>
  render(
    <ApprovalDetails
      request={request(toolName)}
      browserSession={null}
      shellCommand=""
      inputEntries={[]}
      isSaveSkill={false}
      isHighRiskIntercepted={false}
      hideVisualHighlight
      hideCompactPayload
    />,
  );

beforeEach(() => {
  intl.locale = 'en';
  act(() => useConfigStore.setState({ mcpConfigs: [], orgMcpConfigs: [] }));
});

describe('ApprovalDetails connector names', () => {
  it('asks about a connector by the name the user gave it', () => {
    act(() => useConfigStore.setState({ mcpConfigs: [connector('天气')] }));

    renderDetails('mcp__unnamed_dd4acd__get_forecast');

    expect(screen.getByText('Use 天气 — get forecast')).toBeInTheDocument();
  });

  it.each([
    ['en', 'Use a connected service — get forecast'],
    ['zh', '使用 已连接的服务 — get forecast'],
  ] as const)('never shows the internal id of a connector the settings do not know, in %s', (locale, title) => {
    intl.locale = locale;

    const { container } = renderDetails('mcp__unnamed_dd4acd__get_forecast');

    expect(screen.getByText(title)).toBeInTheDocument();
    expect(container.textContent).not.toContain('unnamed');
  });
});
