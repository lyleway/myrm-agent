/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';

import type { ExternalAgentAccount } from '@/services/external-agents';

const stableT = (key: string) => key;

const mocks = vi.hoisted(() => ({
  saved: undefined as unknown,
  set: vi.fn(),
  statuses: [] as unknown[],
  accounts: [] as unknown[],
  list: vi.fn(),
  create: vi.fn(),
  remove: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
  useLocale: () => 'en',
}));
vi.mock('@/services/config', () => ({
  getConfigSyncManager: () => ({ get: () => mocks.saved, set: mocks.set }),
}));
vi.mock('@/services/external-agents', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/services/external-agents')>()),
  getExternalAgentAuthStatus: async () => mocks.statuses,
  listExternalAgentAccounts: mocks.list,
  createExternalAgentAccount: mocks.create,
  deleteExternalAgentAccount: mocks.remove,
}));
vi.mock('@/lib/api', () => ({
  apiRequest: vi.fn(),
  ApiError: class ApiError extends Error {
    code = 0;
  },
}));
vi.mock('@/lib/deploy-mode', () => ({ isLocalMode: () => false }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
// What the row hands the controls is what is under test, not the controls themselves.
vi.mock('../ExternalAgentAuthControls', () => ({
  default: ({ account, accountState }: { account?: string; accountState?: ExternalAgentAccount | null }) => (
    <div data-testid="auth-controls">
      {account ? `${account}:${accountState ? (accountState.authenticated ? 'in' : 'out') : 'unknown'}` : 'default'}
    </div>
  ),
}));

import ExternalAgentsConfig from '../ExternalAgentsConfig';

const agent = (overrides: Record<string, unknown> = {}) => ({
  name: 'claude-code',
  type: 'cli',
  command: 'claude',
  args: ['-p'],
  enabled: true,
  permissionMode: 'safe',
  authMode: 'subscription',
  maxTurns: 25,
  ...overrides,
});

const account = (backend: string, name: string, authenticated = false): ExternalAgentAccount => ({
  backend,
  name,
  authenticated,
  authStatus: authenticated ? 'authenticated' : 'not_authenticated',
});

const hostedStatus = {
  backend: 'claude',
  installed: true,
  path: '/usr/bin/claude',
  version: '1.0.0',
  transport: 'cli',
  authenticated: false,
  authStatus: 'not_authenticated',
  loginStrategy: 'browser_oauth',
  scriptableLogin: true,
  needsCodeInput: false,
  subscriptionLoginAllowed: false,
  availableInDeployment: true,
};

const editorOf = async (index = 0) => {
  render(<ExternalAgentsConfig />);
  await userEvent.click((await screen.findAllByRole('button', { name: 'edit' }))[index]);
};

const picker = () => screen.getByRole('combobox', { name: 'account' });

const choose = async (name: string) => {
  await userEvent.click(picker());
  await userEvent.click(screen.getByRole('option', { name }));
};

const savedAgents = (call = 0) => {
  const [key, value] = mocks.set.mock.calls[call] as [string, { agents: Record<string, unknown>[] }];
  expect(key).toBe('externalAgents');
  return value.agents;
};

describe('ExternalAgentsConfig accounts', () => {
  beforeEach(() => {
    mocks.statuses = [];
    mocks.accounts = [];
    mocks.saved = { agents: [agent()] };
    mocks.set.mockClear();
    mocks.list.mockReset();
    mocks.list.mockImplementation(async () => mocks.accounts);
    mocks.create.mockReset();
    mocks.remove.mockReset();
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
  });

  describe('on the rows', () => {
    it('hands each agent the account it runs as and what the server reports for it', async () => {
      mocks.saved = {
        agents: [
          agent({ name: 'one', account: 'work' }),
          agent({ name: 'two' }),
          agent({ name: 'three', command: 'codex', account: 'work' }),
          agent({ name: 'four', account: 'new-here' }),
        ],
      };
      mocks.accounts = [account('claude', 'work', true), account('codex', 'work', false)];
      render(<ExternalAgentsConfig />);

      await vi.waitFor(() =>
        expect(screen.getAllByTestId('auth-controls').map((node) => node.textContent)).toEqual([
          'work:in',
          'default',
          'work:out',
          'new-here:unknown',
        ]),
      );
    });

    it('gives no account to an agent that runs on an API key or on a command the app does not know', async () => {
      mocks.saved = {
        agents: [
          agent({ name: 'one', authMode: 'api_key', account: 'work' }),
          agent({ name: 'two', command: 'aider', account: 'work' }),
        ],
      };
      mocks.accounts = [account('claude', 'work', true)];
      render(<ExternalAgentsConfig />);

      await vi.waitFor(() => expect(mocks.list).toHaveBeenCalled());
      expect(screen.getAllByTestId('auth-controls').map((node) => node.textContent)).toEqual(['default', 'default']);
    });

    it('gives no account to any agent where the deployment keeps no subscription login', async () => {
      mocks.saved = { agents: [agent({ account: 'work' })] };
      mocks.statuses = [hostedStatus];
      mocks.accounts = [account('claude', 'work', true)];
      render(<ExternalAgentsConfig />);

      await vi.waitFor(() => expect(screen.getByTestId('auth-controls')).toHaveTextContent('default'));
    });
  });

  describe('in the editor', () => {
    it('stores the account chosen for a known agent on its subscription login', async () => {
      mocks.accounts = [account('claude', 'work')];
      await editorOf();
      await vi.waitFor(() => expect(mocks.list).toHaveBeenCalled());

      await choose('work');
      await userEvent.click(screen.getByRole('button', { name: 'save' }));

      expect(savedAgents()[0]).toMatchObject({ name: 'claude-code', account: 'work' });
    });

    it('stores no account once the login of this machine is chosen again', async () => {
      mocks.saved = { agents: [agent({ account: 'work' })] };
      mocks.accounts = [account('claude', 'work')];
      await editorOf();

      await choose('accountDefault');
      await userEvent.click(screen.getByRole('button', { name: 'save' }));

      expect(savedAgents()[0]).not.toHaveProperty('account');
    });

    it.each([
      ['an API key', agent({ authMode: 'api_key', account: 'work' })],
      ['a command the app does not know', agent({ command: 'aider', account: 'work' })],
    ])('offers no account to an agent on %s, and drops one left over', async (_label, stored) => {
      mocks.saved = { agents: [stored] };
      await editorOf();

      expect(screen.queryByTestId('external-agent-account')).not.toBeInTheDocument();
      await userEvent.click(screen.getByRole('button', { name: 'save' }));

      expect(savedAgents()[0]).not.toHaveProperty('account');
    });

    it('reads the accounts again once one is made, so its login state is known at once', async () => {
      mocks.create.mockResolvedValue(account('claude', 'work'));
      await editorOf();
      await vi.waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(1));

      await choose('accountNew');
      await userEvent.type(screen.getByLabelText('accountNameLabel'), 'work');
      await userEvent.click(screen.getByRole('button', { name: 'accountCreate' }));

      expect(mocks.create).toHaveBeenCalledWith('claude', 'work');
      await vi.waitFor(() => expect(mocks.list).toHaveBeenCalledTimes(2));
    });

    it('takes a deleted account off every agent that ran as it, and off no other', async () => {
      mocks.saved = {
        agents: [
          agent({ name: 'one', account: 'work' }),
          agent({ name: 'two', account: 'work' }),
          agent({ name: 'three', command: 'codex', account: 'work' }),
          agent({ name: 'four', account: 'home' }),
        ],
      };
      mocks.accounts = [account('claude', 'work'), account('claude', 'home')];
      mocks.remove.mockResolvedValue(undefined);
      await editorOf();

      await userEvent.click(screen.getByRole('button', { name: 'accountDelete' }));
      await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'delete' }));

      expect(mocks.remove).toHaveBeenCalledWith('claude', 'work');
      await vi.waitFor(() => expect(mocks.set).toHaveBeenCalledTimes(1));
      expect(savedAgents().map(({ name, account: stored }) => [name, stored])).toEqual([
        ['one', undefined],
        ['two', undefined],
        ['three', 'work'],
        ['four', 'home'],
      ]);

      // The form no longer holds the account either, so saving does not bring it back.
      await userEvent.click(screen.getByRole('button', { name: 'save' }));
      expect(savedAgents(1).map(({ name, account: stored }) => [name, stored])).toEqual([
        ['one', undefined],
        ['two', undefined],
        ['three', 'work'],
        ['four', 'home'],
      ]);
    });

    it('leaves every agent as it was when the account could not be deleted', async () => {
      mocks.saved = { agents: [agent({ account: 'work' })] };
      mocks.accounts = [account('claude', 'work')];
      mocks.remove.mockRejectedValue(new Error('boom'));
      await editorOf();

      await userEvent.click(screen.getByRole('button', { name: 'accountDelete' }));
      await userEvent.click(within(await screen.findByRole('alertdialog')).getByRole('button', { name: 'delete' }));

      await vi.waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith('accountDeleteFailed'));
      expect(mocks.set).not.toHaveBeenCalled();
    });
  });
});
