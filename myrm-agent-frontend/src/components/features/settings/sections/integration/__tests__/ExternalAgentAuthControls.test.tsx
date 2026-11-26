/** @vitest-environment jsdom */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAuthStatus } from '@/services/external-agents';

const stableT = (key: string) => key;

const mocks = vi.hoisted(() => ({
  logout: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('@/services/external-agents', () => ({ logoutExternalAgent: mocks.logout }));
vi.mock('../ExternalAgentInstallDialog', () => ({
  default: ({ open, backend }: { open: boolean; backend: string }) => (open ? <div>install-dialog:{backend}</div> : null),
}));
vi.mock('../ExternalAgentLoginDialog', () => ({
  default: ({ open, command }: { open: boolean; command: string }) => (open ? <div>login-dialog:{command}</div> : null),
}));

import ExternalAgentAuthControls from '../ExternalAgentAuthControls';

const status = (overrides: Partial<ExternalAgentAuthStatus> = {}): ExternalAgentAuthStatus => ({
  backend: 'codex',
  installed: true,
  path: '/usr/local/bin/codex',
  version: '0.5.0',
  authenticated: false,
  authStatus: 'not_authenticated',
  loginStrategy: 'device_code',
  scriptableLogin: true,
  needsCodeInput: false,
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

    expect(mocks.logout).toHaveBeenCalledWith('codex');
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

  it('keeps the install dialog mounted across the installed transition so a finished install is not cut short', async () => {
    const { rerender } = render(
      <ExternalAgentAuthControls
        command="codex"
        status={status({ installed: false })}
        onChanged={vi.fn()}
      />,
    );
    await userEvent.click(screen.getByRole('button', { name: 'install' }));
    expect(screen.getByText('install-dialog:codex')).toBeInTheDocument();

    rerender(<ExternalAgentAuthControls command="codex" status={status({ installed: true })} onChanged={vi.fn()} />);

    expect(screen.getByText('install-dialog:codex')).toBeInTheDocument();
    expect(screen.getByText('badgeLoggedOut')).toBeInTheDocument();
  });
});
