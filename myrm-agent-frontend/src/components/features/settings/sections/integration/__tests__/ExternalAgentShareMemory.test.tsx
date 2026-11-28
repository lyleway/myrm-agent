/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

const stableT = (key: string) => key;

const mocks = vi.hoisted(() => ({
  saved: undefined as unknown,
  set: vi.fn(),
}));

vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
  useLocale: () => 'en',
}));
vi.mock('@/services/config', () => ({
  getConfigSyncManager: () => ({ get: () => mocks.saved, set: mocks.set }),
}));
vi.mock('@/services/external-agents', () => ({
  getExternalAgentAuthStatus: async () => [],
  listExternalAgentAccounts: async () => [],
}));
vi.mock('@/lib/api', () => ({ apiRequest: vi.fn() }));
vi.mock('@/lib/deploy-mode', () => ({ isLocalMode: () => false }));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));
vi.mock('../ExternalAgentAuthControls', () => ({ default: () => null }));

import ExternalAgentsConfig from '../ExternalAgentsConfig';

const agent = (overrides: Record<string, unknown> = {}) => ({
  name: 'gemini-cli',
  type: 'acp',
  command: 'gemini',
  args: [],
  enabled: true,
  permissionMode: 'safe',
  authMode: 'api_key',
  maxTurns: 25,
  ...overrides,
});

const openEditor = async () => {
  render(<ExternalAgentsConfig />);
  await userEvent.click(await screen.findByRole('button', { name: 'edit' }));
};

const memorySwitch = () => screen.getByRole('switch', { name: 'shareMemory' });

const savedAgent = () => {
  expect(mocks.set).toHaveBeenCalledTimes(1);
  const [key, value] = mocks.set.mock.calls[0] as [string, { agents: Record<string, unknown>[] }];
  expect(key).toBe('externalAgents');
  return value.agents[0];
};

describe('ExternalAgentsConfig sharing memory with an agent', () => {
  beforeEach(() => {
    mocks.set.mockClear();
    mocks.saved = { agents: [agent()] };
  });

  it('offers it off, with what it means said next to the switch', async () => {
    await openEditor();

    expect(memorySwitch()).not.toBeChecked();
    expect(screen.getByText('shareMemoryHint')).toBeInTheDocument();
  });

  it('tells a Gemini CLI user that it only connects in folders it trusts', async () => {
    await openEditor();

    expect(screen.getByText('shareMemoryTrustedFolderHint')).toBeInTheDocument();
  });

  it('says nothing about trusted folders for an agent that does not need them', async () => {
    mocks.saved = { agents: [agent({ name: 'claude-code', type: 'cli', command: 'claude' })] };
    await openEditor();

    expect(screen.getByText('shareMemoryHint')).toBeInTheDocument();
    expect(screen.queryByText('shareMemoryTrustedFolderHint')).not.toBeInTheDocument();
  });

  it('stores the consent once the user turns it on', async () => {
    await openEditor();

    await userEvent.click(memorySwitch());
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent().shareMemory).toBe(true);
  });

  it('stores nothing about memory for an agent the user never turned it on for', async () => {
    await openEditor();

    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent()).not.toHaveProperty('shareMemory');
  });

  it('shows a stored consent as on and takes it back when the user turns it off', async () => {
    mocks.saved = { agents: [agent({ shareMemory: true })] };
    await openEditor();
    expect(memorySwitch()).toBeChecked();

    await userEvent.click(memorySwitch());
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent()).not.toHaveProperty('shareMemory');
  });
});
