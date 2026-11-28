/** @vitest-environment jsdom */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAuthEvent, ExternalAgentAuthStatus } from '@/services/external-agents';

const stableT = (key: string, values?: Record<string, string>) =>
  values ? `${key}:${Object.values(values).join('/')}` : key;

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

const deploy = vi.hoisted(() => ({
  isTauriRuntime: vi.fn(),
  isLoopbackDevHost: vi.fn(),
  isRemoteGatewayActive: vi.fn(),
}));

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('@/lib/deploy-mode', () => deploy);
vi.mock('@/services/external-agents', () => ({
  streamExternalAgentLogin: mocks.stream,
}));

import ExternalAgentLoginDialog from '../ExternalAgentLoginDialog';

const status = (overrides: Partial<ExternalAgentAuthStatus> = {}): ExternalAgentAuthStatus => ({
  backend: 'codex',
  installed: true,
  path: '/usr/local/bin/codex',
  version: '0.5.0',
  transport: 'app_server',
  authenticated: false,
  authStatus: 'not_authenticated',
  loginStrategy: 'browser_oauth',
  subscriptionLoginAllowed: true,
  availableInDeployment: true,
  ...overrides,
});

const renderDialog = (
  overrides: Partial<React.ComponentProps<typeof ExternalAgentLoginDialog>> & { status?: ExternalAgentAuthStatus } = {},
) => {
  const props = {
    open: true,
    onOpenChange: vi.fn(),
    command: 'codex',
    status: status(),
    onChanged: vi.fn(),
    ...overrides,
  };
  render(<ExternalAgentLoginDialog {...props} />);
  return props;
};

describe('ExternalAgentLoginDialog', () => {
  beforeEach(() => {
    mocks.stream.mockReset();
    mocks.stream.mockResolvedValue(undefined);
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
    deploy.isTauriRuntime.mockReturnValue(false);
    deploy.isLoopbackDevHost.mockReturnValue(true);
    deploy.isRemoteGatewayActive.mockReturnValue(false);
  });

  it('logs in to the login of this machine when no account is named', async () => {
    renderDialog();

    await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    expect(mocks.stream.mock.calls[0][0]).toEqual({
      command: 'codex',
      backend: 'codex',
      account: undefined,
      remote: false,
    });
    expect(screen.getByText('loginTitle:codex')).toBeInTheDocument();
  });

  it('logs in to the named account, and says which one in the title', async () => {
    renderDialog({ account: 'work' });

    await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    expect(mocks.stream.mock.calls[0][0]).toMatchObject({ backend: 'codex', account: 'work' });
    expect(screen.getByText('loginTitleAccount:codex/work')).toBeInTheDocument();
  });

  describe('which login the CLI is asked for', () => {
    const remoteAsked = async (): Promise<unknown> => {
      renderDialog();
      await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
      return mocks.stream.mock.calls[0][0].remote;
    };

    it('is the one for a browser on this machine on a page served from this machine', async () => {
      expect(await remoteAsked()).toBe(false);
    });

    it('is the one for a browser on this machine in the desktop app', async () => {
      deploy.isTauriRuntime.mockReturnValue(true);
      deploy.isLoopbackDevHost.mockReturnValue(false);

      expect(await remoteAsked()).toBe(false);
    });

    it('is the one that works from anywhere on a page served from another host', async () => {
      deploy.isLoopbackDevHost.mockReturnValue(false);

      expect(await remoteAsked()).toBe(true);
    });

    it('is the one that works from anywhere when the desktop app talks to a remote server', async () => {
      deploy.isTauriRuntime.mockReturnValue(true);
      deploy.isRemoteGatewayActive.mockReturnValue(true);

      expect(await remoteAsked()).toBe(true);
    });
  });

  it('keeps the link on screen when the code arrives on a later line', async () => {
    mocks.stream.mockImplementation(async (_params, onEvent: (event: ExternalAgentAuthEvent) => void) => {
      onEvent({ type: 'prompt', message: 'Open this link', url: 'https://auth.example.com/device' });
      onEvent({ type: 'prompt', message: 'Enter this one-time code', code: 'WXYZ-12345' });
    });
    renderDialog();

    expect(await screen.findByRole('link', { name: 'https://auth.example.com/device' })).toBeInTheDocument();
    expect(screen.getByText('WXYZ-12345')).toBeInTheDocument();
  });

  it("shows the CLI's own words when the login fails, with a way to try again", async () => {
    const reason = "Couldn't log in: the sign-in was not completed.";
    mocks.stream.mockImplementation(async (_params, onEvent: (event: ExternalAgentAuthEvent) => void) => {
      onEvent({ type: 'error', message: reason });
    });
    renderDialog();

    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(mocks.toast.error).toHaveBeenCalledWith(reason);

    await userEvent.click(screen.getByRole('button', { name: 'loginRetry' }));

    await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(2));
  });

  it('reports a finished login so the badges are read again', async () => {
    const success: ExternalAgentAuthEvent = { type: 'success', message: 'done' };
    mocks.stream.mockImplementation(async (_params, onEvent: (event: ExternalAgentAuthEvent) => void) => {
      onEvent(success);
    });
    const { onChanged } = renderDialog({ account: 'work' });

    await vi.waitFor(() => expect(onChanged).toHaveBeenCalledTimes(1));
    expect(mocks.toast.success).toHaveBeenCalledWith('loginSuccess');
  });

  it('has no box to paste a code or a credential into', async () => {
    mocks.stream.mockImplementation(() => new Promise<void>(() => undefined));
    renderDialog();

    await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    expect(screen.queryByRole('textbox')).not.toBeInTheDocument();
  });
});
