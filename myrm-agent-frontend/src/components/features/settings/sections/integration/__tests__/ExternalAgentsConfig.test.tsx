/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ExternalAgentType } from '@/services/config';

const stableT = (key: string) => key;

const mocks = vi.hoisted(() => ({
  saved: undefined as unknown,
  set: vi.fn(),
  statuses: [] as unknown[],
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
  useLocale: () => 'en',
}));
vi.mock('@/services/config', () => ({
  getConfigSyncManager: () => ({ get: () => mocks.saved, set: mocks.set }),
}));
vi.mock('@/services/external-agents', () => ({
  getExternalAgentAuthStatus: async () => mocks.statuses,
  listExternalAgentAccounts: async () => [],
}));
vi.mock('@/lib/api', () => ({ apiRequest: vi.fn() }));
vi.mock('@/lib/deploy-mode', () => ({ isLocalMode: () => false }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('../ExternalAgentAuthControls', () => ({ default: () => null }));

import ExternalAgentsConfig from '../ExternalAgentsConfig';

const agent = (overrides: Record<string, unknown> = {}) => ({
  name: 'claude-code',
  type: 'cli',
  command: 'claude',
  args: ['-p'],
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

const savedAgent = () => {
  expect(mocks.set).toHaveBeenCalledTimes(1);
  const [key, value] = mocks.set.mock.calls[0] as [string, { agents: Record<string, unknown>[] }];
  expect(key).toBe('externalAgents');
  return value.agents[0];
};

describe('ExternalAgentsConfig environment variables', () => {
  beforeEach(() => {
    mocks.statuses = [];
    mocks.set.mockClear();
    mocks.toast.error.mockClear();
    mocks.saved = { agents: [agent({ env: { ANTHROPIC_API_KEY: 'sk-old', HTTPS_PROXY: 'http://proxy:8080' } })] };
  });

  it('opens the stored variables for editing, values masked', async () => {
    await openEditor();

    const group = screen.getByRole('group', { name: 'env' });
    expect(
      within(group)
        .getAllByLabelText('keyPlaceholder')
        .map((input) => (input as HTMLInputElement).value),
    ).toEqual(['ANTHROPIC_API_KEY', 'HTTPS_PROXY']);
    for (const value of within(group).getAllByLabelText('valuePlaceholder')) {
      expect(value).toHaveAttribute('type', 'password');
    }
  });

  it('saves a changed value together with the agent', async () => {
    await openEditor();
    const [key] = screen.getAllByLabelText('valuePlaceholder');

    await userEvent.clear(key);
    await userEvent.type(key, 'sk-new');
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent().env).toEqual({ ANTHROPIC_API_KEY: 'sk-new', HTTPS_PROXY: 'http://proxy:8080' });
  });

  it('stores no environment once every variable is removed', async () => {
    await openEditor();

    for (const remove of screen.getAllByRole('button', { name: 'envRemove' })) {
      await userEvent.click(remove);
    }
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent()).not.toHaveProperty('env');
  });

  it('saves a variable added to an agent that had none', async () => {
    mocks.saved = { agents: [agent()] };
    await openEditor();

    await userEvent.click(screen.getByRole('button', { name: 'envAdd' }));
    await userEvent.type(screen.getByLabelText('keyPlaceholder'), 'OPENAI_API_KEY');
    await userEvent.type(screen.getByLabelText('valuePlaceholder'), 'sk-1');
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent().env).toEqual({ OPENAI_API_KEY: 'sk-1' });
  });

  it('leaves out a line that was added and never filled in', async () => {
    mocks.saved = { agents: [agent()] };
    await openEditor();

    await userEvent.click(screen.getByRole('button', { name: 'envAdd' }));
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent()).not.toHaveProperty('env');
  });

  it('refuses to save a name that cannot be used and says why', async () => {
    mocks.saved = { agents: [agent()] };
    await openEditor();

    await userEvent.click(screen.getByRole('button', { name: 'envAdd' }));
    await userEvent.type(screen.getByLabelText('keyPlaceholder'), '1BAD');
    await userEvent.type(screen.getByLabelText('valuePlaceholder'), 'v');
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(mocks.set).not.toHaveBeenCalled();
    expect(mocks.toast.error).toHaveBeenCalledWith('envFixBeforeSave');
    expect(screen.getByText('envNameInvalid')).toBeInTheDocument();
  });

  it('keeps the settings of the agent that the form does not show', async () => {
    mocks.saved = { agents: [agent({ timeout: 900, maxResponseChars: 20000, env: { A: '1' } })] };
    await openEditor();

    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent()).toMatchObject({ timeout: 900, maxResponseChars: 20000, env: { A: '1' } });
  });

  it('starts a new agent without variables and keeps typed ones when a preset is chosen', async () => {
    mocks.saved = { agents: [] };
    render(<ExternalAgentsConfig />);
    await userEvent.click(await screen.findByRole('button', { name: 'add' }));
    expect(screen.queryByLabelText('keyPlaceholder')).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'envAdd' }));
    await userEvent.type(screen.getByLabelText('keyPlaceholder'), 'HTTPS_PROXY');
    await userEvent.click(screen.getByRole('button', { name: 'preset_claude' }));

    expect(screen.getByLabelText('keyPlaceholder')).toHaveValue('HTTPS_PROXY');
    expect(screen.getByDisplayValue('claude-code')).toBeInTheDocument();
  });
});

describe('ExternalAgentsConfig auth mode', () => {
  const status = (backend: string, subscriptionLoginAllowed: boolean) => ({
    backend,
    installed: true,
    path: `/usr/bin/${backend}`,
    version: '1.0.0',
    transport: 'cli',
    authenticated: false,
    authStatus: 'not_authenticated',
    loginStrategy: 'browser_oauth',
    scriptableLogin: true,
    needsCodeInput: false,
    subscriptionLoginAllowed,
  });

  beforeEach(() => {
    mocks.set.mockClear();
    mocks.saved = { agents: [agent({ authMode: 'subscription' })] };
  });

  const openAuthModeSelect = async () => {
    await openEditor();
    // The server's answer arrives after the first render.
    await vi.waitFor(() => expect(screen.getByText('authModeHostedHint', { exact: false })).toBeTruthy());
  };

  const authModeOptions = async () => {
    const trigger = screen.getAllByRole('combobox').find((select) => /^authMode/.test(select.textContent ?? ''));
    await userEvent.click(trigger as HTMLElement);
    return screen.getAllByRole('option').map((option) => option.textContent);
  };

  it('offers subscription and API key where login is allowed', async () => {
    mocks.statuses = [status('claude', true), status('codex', true)];
    await openEditor();

    expect(await screen.findByText('authModeHint')).toBeInTheDocument();
    expect(screen.queryByText('authModeHostedHint')).not.toBeInTheDocument();
    expect(await authModeOptions()).toEqual(['authModeSubscription', 'authModeApiKey']);
  });

  it('offers only the API key where the deployment keeps no subscription login, and stores it', async () => {
    mocks.statuses = [status('claude', false), status('codex', false)];
    await openAuthModeSelect();

    expect(screen.queryByText('authModeHint')).not.toBeInTheDocument();
    expect(await authModeOptions()).toEqual(['authModeApiKey']);
    await userEvent.keyboard('{Escape}');

    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent().authMode).toBe('api_key');
  });
});

describe('ExternalAgentsConfig transport', () => {
  const status = (backend: string, transport: ExternalAgentType) => ({
    backend,
    installed: true,
    path: `/usr/bin/${backend}`,
    version: '1.0.0',
    transport,
    authenticated: false,
    authStatus: 'not_authenticated',
    loginStrategy: 'browser_oauth',
    scriptableLogin: true,
    needsCodeInput: false,
    subscriptionLoginAllowed: true,
  });

  const typeSelect = () => within(screen.getByTestId('external-agent-type')).getByRole('combobox');

  beforeEach(() => {
    mocks.set.mockClear();
    mocks.statuses = [status('claude', 'cli'), status('gemini', 'acp'), status('codex', 'app_server')];
  });

  it('shows a known agent under the transport the app fixes for it and stores that one', async () => {
    mocks.saved = { agents: [agent({ name: 'gemini-cli', command: 'gemini', type: 'cli', args: [] })] };
    await openEditor();

    await vi.waitFor(() => expect(typeSelect()).toBeDisabled());
    expect(typeSelect()).toHaveTextContent('typeAcp');
    expect(screen.getByText('typeFixedHint')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent().type).toBe('acp');
  });

  it('adds Gemini from its preset as the agent it is, with no arguments of ours', async () => {
    mocks.saved = { agents: [] };
    render(<ExternalAgentsConfig />);
    await userEvent.click(await screen.findByRole('button', { name: 'add' }));
    await userEvent.click(screen.getByRole('button', { name: 'preset_gemini' }));

    await vi.waitFor(() => expect(typeSelect()).toBeDisabled());
    expect(typeSelect()).toHaveTextContent('typeAcp');
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent()).toMatchObject({ name: 'gemini-cli', command: 'gemini', type: 'acp', args: [] });
  });

  it('adds Codex from its preset as the agent it is, with no arguments of ours', async () => {
    mocks.saved = { agents: [] };
    render(<ExternalAgentsConfig />);
    await userEvent.click(await screen.findByRole('button', { name: 'add' }));
    await userEvent.click(screen.getByRole('button', { name: 'preset_codex' }));

    await vi.waitFor(() => expect(typeSelect()).toBeDisabled());
    expect(typeSelect()).toHaveTextContent('typeAppServer');
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent()).toMatchObject({ name: 'codex-cli', command: 'codex', type: 'app_server', args: [] });
  });

  it('shows a Codex agent stored as a command-line agent under its app server and stores that one', async () => {
    mocks.saved = { agents: [agent({ name: 'codex-cli', command: 'codex', type: 'cli', args: ['exec', '--json'] })] };
    await openEditor();

    await vi.waitFor(() => expect(typeSelect()).toBeDisabled());
    expect(typeSelect()).toHaveTextContent('typeAppServer');
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent().type).toBe('app_server');
  });

  it('leaves the type to the user for a command that only carries the name of a known agent', async () => {
    mocks.saved = { agents: [agent({ name: 'wrapped', command: 'gemini-wrapper', type: 'cli', args: [] })] };
    await openEditor();

    await vi.waitFor(() => expect(screen.getByText('authModeHint', { exact: false })).toBeTruthy());
    expect(typeSelect()).toBeEnabled();
    expect(screen.queryByText('typeFixedHint')).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'save' }));

    expect(savedAgent().type).toBe('cli');
  });
});
