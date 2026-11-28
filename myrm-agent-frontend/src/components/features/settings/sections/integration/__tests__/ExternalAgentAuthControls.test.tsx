/** @vitest-environment jsdom */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAccount, ExternalAgentAuthStatus } from '@/services/external-agents';

const stableT = (key: string) => key;

const mocks = vi.hoisted(() => ({
  logout: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('@/services/external-agents', () => ({ logoutExternalAgent: mocks.logout }));
vi.mock('../ExternalAgentInstallDialog', () => ({
  default: ({ open, backend }: { open: boolean; backend: string }) =>
    open ? <div>install-dialog:{backend}</div> : null,
}));
vi.mock('../ExternalAgentLoginDialog', () => ({
  default: ({ open, command, account }: { open: boolean; command: string; account?: string }) =>
    open ? <div>{account ? `login-dialog:${command}:${account}` : `login-dialog:${command}`}</div> : null,
}));
vi.mock('../ExternalAgentTerminalLoginDialog', () => ({
  default: ({ open, status, account }: { open: boolean; status: ExternalAgentAuthStatus; account?: string }) =>
    open ? (
      <div>
        {account ? `terminal-login-dialog:${status.backend}:${account}` : `terminal-login-dialog:${status.backend}`}
      </div>
    ) : null,
}));

import ExternalAgentAuthControls from '../ExternalAgentAuthControls';

const status = (overrides: Partial<ExternalAgentAuthStatus> = {}): ExternalAgentAuthStatus => ({
  backend: 'codex',
  installed: true,
  path: '/usr/local/bin/codex',
  version: '0.5.0',
  transport: 'cli',
  authenticated: false,
  authStatus: 'not_authenticated',
  loginStrategy: 'browser_oauth',
  subscriptionLoginAllowed: true,
  availableInDeployment: true,
  ...overrides,
});

describe('ExternalAgentAuthControls', () => {
  beforeEach(() => {
    mocks.logout.mockReset();
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
  });

  it('renders nothing before the server has reported on the backend', () => {
    const { container } = render(<ExternalAgentAuthControls command="codex" status={null} onChanged={vi.fn()} />);

    expect(container).toBeEmptyDOMElement();
  });

  it('offers installation, and only installation, while the CLI is missing', async () => {
    render(
      <ExternalAgentAuthControls
        command="codex"
        status={status({ installed: false, path: null, version: null })}
        onChanged={vi.fn()}
      />,
    );

    expect(screen.queryByText('badgeLoggedOut')).not.toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'login' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'install' }));

    expect(screen.getByText('install-dialog:codex')).toBeInTheDocument();
  });

  it('shows the logged-out badge with a login entry that opens the login dialog', async () => {
    render(<ExternalAgentAuthControls command="codex" status={status()} onChanged={vi.fn()} />);

    expect(screen.getByText('badgeLoggedOut')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'logout' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'login' }));

    expect(screen.getByText('login-dialog:codex')).toBeInTheDocument();
  });

  it('opens the terminal login, not the one the app drives, for an agent whose login the user runs in a terminal', async () => {
    render(
      <ExternalAgentAuthControls
        command="claude"
        status={status({ backend: 'claude', loginStrategy: 'terminal' })}
        onChanged={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'login' }));

    expect(screen.getByText('terminal-login-dialog:claude')).toBeInTheDocument();
    expect(screen.queryByText('login-dialog:claude')).not.toBeInTheDocument();
  });

  it('logs in to the named account in the terminal login too', async () => {
    render(
      <ExternalAgentAuthControls
        command="claude"
        status={status({ backend: 'claude', loginStrategy: 'terminal' })}
        account="work"
        onChanged={vi.fn()}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'login' }));

    expect(screen.getByText('terminal-login-dialog:claude:work')).toBeInTheDocument();
  });

  it('shows the logged-in badge keyed on the CLI-reported login state, with logout', async () => {
    const onChanged = vi.fn();
    mocks.logout.mockResolvedValue({ backend: 'codex', authenticated: false, authStatus: 'not_authenticated' });
    render(
      <ExternalAgentAuthControls
        command="codex"
        status={status({ authenticated: true, authStatus: 'authenticated' })}
        onChanged={onChanged}
      />,
    );

    expect(screen.getByText('badgeLoggedIn')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'login' })).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'logout' }));

    expect(mocks.logout).toHaveBeenCalledWith('codex', undefined);
    expect(mocks.toast.success).toHaveBeenCalledWith('logoutSuccess');
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  it('reports a failed logout without refreshing as if it had worked', async () => {
    const onChanged = vi.fn();
    mocks.logout.mockRejectedValue(new Error('boom'));
    render(
      <ExternalAgentAuthControls
        command="codex"
        status={status({ authenticated: true, authStatus: 'authenticated' })}
        onChanged={onChanged}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'logout' }));

    expect(mocks.toast.error).toHaveBeenCalledWith('loginFailed');
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('says so when the CLI still reports the login after a logout, and refreshes to show it', async () => {
    const onChanged = vi.fn();
    mocks.logout.mockResolvedValue({ backend: 'claude', authenticated: true, authStatus: 'authenticated' });
    render(
      <ExternalAgentAuthControls
        command="claude"
        status={status({ backend: 'claude', authenticated: true, authStatus: 'authenticated' })}
        onChanged={onChanged}
      />,
    );

    await userEvent.click(screen.getByRole('button', { name: 'logout' }));

    expect(mocks.toast.error).toHaveBeenCalledWith('logoutIncomplete');
    expect(mocks.toast.success).not.toHaveBeenCalled();
    expect(onChanged).toHaveBeenCalledTimes(1);
  });

  describe('in a hosted deployment', () => {
    const hosted = (overrides: Partial<ExternalAgentAuthStatus> = {}) =>
      status({ backend: 'gemini', subscriptionLoginAllowed: false, ...overrides });

    it('offers no login for an installed agent, since agents run on an API key there', () => {
      const { container } = render(
        <ExternalAgentAuthControls command="gemini" status={hosted()} onChanged={vi.fn()} />,
      );

      expect(screen.queryByRole('button', { name: 'login' })).not.toBeInTheDocument();
      expect(screen.queryByText('badgeLoggedOut')).not.toBeInTheDocument();
      expect(screen.queryByText('login-dialog:gemini')).not.toBeInTheDocument();
      expect(container.querySelector('button')).toBeNull();
    });

    it('still offers installation of a missing agent', async () => {
      render(
        <ExternalAgentAuthControls
          command="gemini"
          status={hosted({ installed: false, path: null, version: null })}
          onChanged={vi.fn()}
        />,
      );

      await userEvent.click(screen.getByRole('button', { name: 'install' }));

      expect(screen.getByText('install-dialog:gemini')).toBeInTheDocument();
    });

    it('shows a login left from before with a way to remove it, and no way to log in again', async () => {
      const onChanged = vi.fn();
      mocks.logout.mockResolvedValue({ backend: 'gemini', authenticated: false, authStatus: 'not_authenticated' });
      render(
        <ExternalAgentAuthControls
          command="gemini"
          status={hosted({ authenticated: true, authStatus: 'authenticated' })}
          onChanged={onChanged}
        />,
      );

      expect(screen.getByText('badgeLoggedIn')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'login' })).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: 'logout' }));

      expect(mocks.logout).toHaveBeenCalledWith('gemini', undefined);
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  describe('for an agent the deployment does not run', () => {
    const blocked = (overrides: Partial<ExternalAgentAuthStatus> = {}) =>
      status({
        backend: 'claude',
        installed: false,
        path: null,
        version: null,
        subscriptionLoginAllowed: false,
        availableInDeployment: false,
        ...overrides,
      });

    it('says it is not available, and offers neither installation nor login', () => {
      const { container } = render(
        <ExternalAgentAuthControls command="claude" status={blocked()} onChanged={vi.fn()} />,
      );

      expect(screen.getByText('blockedInDeployment')).toBeInTheDocument();
      expect(container.querySelector('button')).toBeNull();
      expect(screen.queryByText('badgeLoggedOut')).not.toBeInTheDocument();
    });

    it('does not offer to log in even when the agent is installed and signed out', () => {
      render(
        <ExternalAgentAuthControls
          command="claude"
          status={blocked({ installed: true, path: '/bin/claude' })}
          onChanged={vi.fn()}
        />,
      );

      expect(screen.queryByRole('button')).not.toBeInTheDocument();
    });

    it('still lets a login left from before be removed', async () => {
      const onChanged = vi.fn();
      mocks.logout.mockResolvedValue({ backend: 'claude', authenticated: false, authStatus: 'not_authenticated' });
      render(
        <ExternalAgentAuthControls
          command="claude"
          status={blocked({ authenticated: true, authStatus: 'authenticated' })}
          onChanged={onChanged}
        />,
      );

      expect(screen.getByText('blockedInDeployment')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'login' })).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: 'logout' }));

      expect(mocks.logout).toHaveBeenCalledWith('claude', undefined);
      expect(mocks.toast.success).toHaveBeenCalledWith('logoutSuccess');
      expect(onChanged).toHaveBeenCalledTimes(1);
    });
  });

  describe('for an agent that runs as a named account', () => {
    const accountState = (authenticated: boolean): ExternalAgentAccount => ({
      backend: 'codex',
      name: 'work',
      authenticated,
      authStatus: authenticated ? 'authenticated' : 'not_authenticated',
    });

    it('names the account and keys the badge on that account, not on the login of this machine', async () => {
      render(
        <ExternalAgentAuthControls
          command="codex"
          status={status({ authenticated: true, authStatus: 'authenticated' })}
          account="work"
          accountState={accountState(false)}
          onChanged={vi.fn()}
        />,
      );

      expect(screen.getByText('work')).toBeInTheDocument();
      expect(screen.getByText('badgeLoggedOut')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'logout' })).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: 'login' }));

      expect(screen.getByText('login-dialog:codex:work')).toBeInTheDocument();
    });

    it('shows the account as logged in on its own login and logs out of that account only', async () => {
      const onChanged = vi.fn();
      mocks.logout.mockResolvedValue({ backend: 'codex', authenticated: false, authStatus: 'not_authenticated' });
      render(
        <ExternalAgentAuthControls
          command="codex"
          status={status()}
          account="work"
          accountState={accountState(true)}
          onChanged={onChanged}
        />,
      );

      expect(screen.getByText('badgeLoggedIn')).toBeInTheDocument();

      await userEvent.click(screen.getByRole('button', { name: 'logout' }));

      expect(mocks.logout).toHaveBeenCalledWith('codex', 'work');
      expect(onChanged).toHaveBeenCalledTimes(1);
    });

    it('counts the account as logged out until the server has reported on it', () => {
      render(
        <ExternalAgentAuthControls
          command="codex"
          status={status({ authenticated: true, authStatus: 'authenticated' })}
          account="work"
          onChanged={vi.fn()}
        />,
      );

      expect(screen.getByText('badgeLoggedOut')).toBeInTheDocument();
      expect(screen.getByRole('button', { name: 'login' })).toBeInTheDocument();
    });
  });

  it('keeps the install dialog mounted across the installed transition so a finished install is not cut short', async () => {
    const { rerender } = render(
      <ExternalAgentAuthControls command="codex" status={status({ installed: false })} onChanged={vi.fn()} />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'install' }));
    expect(screen.getByText('install-dialog:codex')).toBeInTheDocument();

    rerender(<ExternalAgentAuthControls command="codex" status={status({ installed: true })} onChanged={vi.fn()} />);

    expect(screen.getByText('install-dialog:codex')).toBeInTheDocument();
    expect(screen.getByText('badgeLoggedOut')).toBeInTheDocument();
  });
});
