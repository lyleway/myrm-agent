'use client';

import { act, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { BuiltinToolsPanel } from '../agent-config-panel/BuiltinToolsPanel';

const mocks = vi.hoisted(() => ({
  agents: [] as { enabled: boolean; command: string }[],
  statuses: [] as unknown[],
}));

vi.mock('next/link', () => ({
  default: ({ children, href }: { children: React.ReactNode; href: string }) => <a href={href}>{children}</a>,
}));
vi.mock('@/hooks/billing/useFeatureEntitlements', () => ({
  useFeatureEntitlements: () => ({ canUseCron: true, isLoading: false }),
}));
vi.mock('../agent-config-panel/MediaCredentialInline', () => ({ MediaCredentialInline: () => null }));
vi.mock('../agent-config-panel/CuPermissionInline', () => ({ CuPermissionInline: () => null }));
vi.mock('@/services/config', () => ({
  getConfigSyncManager: () => ({ get: () => ({ agents: mocks.agents }) }),
}));
vi.mock('@/services/external-agents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/external-agents')>()),
  getExternalAgentAuthStatus: async () => mocks.statuses,
}));
vi.mock('@/lib/deploy-mode', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/deploy-mode')>()),
  isSandbox: () => true,
  isLocalMode: () => false,
}));

const tPanel = (key: string) => key;

const row = (backend: string, availableInDeployment: boolean) => ({
  backend,
  installed: availableInDeployment,
  authenticated: false,
  availableInDeployment,
});

const renderPanel = () =>
  render(
    <BuiltinToolsPanel
      localBuiltinTools={['external_cli']}
      setLocalBuiltinTools={vi.fn()}
      localAutoRestoreDomains={[]}
      setLocalAutoRestoreDomains={vi.fn()}
      setLocalBrowserSource={vi.fn()}
      setLocalDialogPolicy={vi.fn()}
      setLocalSessionRecording={vi.fn()}
      t={(key: string) => key}
      tAgent={(key: string) => key}
      tPanel={tPanel}
    />,
  );

describe('BuiltinToolsPanel external CLI section in a deployment that does not run every agent', () => {
  beforeEach(() => {
    mocks.statuses = [row('claude', false), row('codex', false), row('gemini', true)];
  });

  it('says the configured agents cannot run here when none of them can', async () => {
    mocks.agents = [{ enabled: true, command: 'claude' }];
    renderPanel();

    expect(await screen.findByText('externalCliBlockedHint')).toBeInTheDocument();
    expect(screen.queryByText('externalCliUnavailableHint')).not.toBeInTheDocument();
  });

  it('does not say it while an agent the deployment runs is configured', async () => {
    mocks.agents = [
      { enabled: true, command: 'claude' },
      { enabled: true, command: 'gemini' },
    ];
    renderPanel();

    // The server's answer arrives after the first render; let it be applied before looking.
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });

    expect(screen.getByText('externalCliSetupHint')).toBeInTheDocument();
    expect(screen.queryByText('externalCliBlockedHint')).not.toBeInTheDocument();
    expect(screen.queryByText('externalCliUnavailableHint')).not.toBeInTheDocument();
  });
});
