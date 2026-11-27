/** @vitest-environment jsdom */
import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAuthEvent, ExternalAgentAuthStatus } from '@/services/external-agents';

const stableT = (key: string, values?: Record<string, string>) =>
  values ? `${key}:${Object.values(values).join('/')}` : key;

const mocks = vi.hoisted(() => ({
  stream: vi.fn(),
  importCredential: vi.fn(),
  feed: vi.fn(),
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
  importExternalAgentCredential: mocks.importCredential,
  feedExternalAgentLogin: mocks.feed,
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
  scriptableLogin: true,
  needsCodeInput: false,
  subscriptionLoginAllowed: true,
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
    mocks.importCredential.mockReset();
    mocks.feed.mockReset();
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
      sessionId: expect.any(String),
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
    const reason = "Couldn't log in: That code wasn't accepted.";
    mocks.stream.mockImplementation(async (_params, onEvent: (event: ExternalAgentAuthEvent) => void) => {
      onEvent({ type: 'error', message: reason });
    });
    renderDialog();

    expect(await screen.findByText(reason)).toBeInTheDocument();
    expect(mocks.toast.error).toHaveBeenCalledWith(reason);

    await userEvent.click(screen.getByRole('button', { name: 'loginRetry' }));

    await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(2));
  });

  describe('for a login that asks for a pasted code', () => {
    const pasteStatus = status({ backend: 'claude', loginStrategy: 'paste_code', needsCodeInput: true });

    it('offers the code box only while the login runs', async () => {
      let finish: () => void = () => undefined;
      mocks.stream.mockImplementation(
        () =>
          new Promise<void>((resolve) => {
            finish = resolve;
          }),
      );
      renderDialog({ command: 'claude', status: pasteStatus });

      expect(await screen.findByPlaceholderText('loginCodePlaceholder')).toBeInTheDocument();

      finish();

      await vi.waitFor(() => expect(screen.queryByPlaceholderText('loginCodePlaceholder')).not.toBeInTheDocument());
    });

    it('hands the pasted code to the login that is running, and leaves the box ready for another try', async () => {
      mocks.stream.mockImplementation(() => new Promise<void>(() => undefined));
      mocks.feed.mockResolvedValue({ ok: true });
      renderDialog({ command: 'claude', status: pasteStatus });

      const box = await screen.findByPlaceholderText('loginCodePlaceholder');
      await userEvent.type(box, ' abc#def ');
      await userEvent.click(screen.getByRole('button', { name: 'loginCodeSubmit' }));

      const { sessionId } = mocks.stream.mock.calls[0][0];
      expect(mocks.feed).toHaveBeenCalledWith(sessionId, 'abc#def');
      await vi.waitFor(() => expect(box).toHaveValue(''));
    });
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

  it('imports a pasted credential into the named account and closes', async () => {
    mocks.importCredential.mockResolvedValue({ backend: 'codex', authenticated: true, authStatus: 'authenticated' });
    const { onChanged, onOpenChange } = renderDialog({ account: 'work', status: status({ scriptableLogin: false }) });

    await userEvent.type(screen.getByPlaceholderText('importPlaceholder'), '{{"token":"t"}');
    await userEvent.click(screen.getByRole('button', { name: 'import' }));

    expect(mocks.importCredential).toHaveBeenCalledWith('codex', '{"token":"t"}', undefined, 'work');
    expect(mocks.stream).not.toHaveBeenCalled();
    expect(mocks.toast.success).toHaveBeenCalledWith('importSuccess');
    expect(onChanged).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('imports into the login of this machine when no account is named', async () => {
    mocks.importCredential.mockResolvedValue({ backend: 'codex', authenticated: true, authStatus: 'authenticated' });
    renderDialog({ status: status({ scriptableLogin: false }) });

    await userEvent.type(screen.getByPlaceholderText('importPlaceholder'), 'blob');
    await userEvent.click(screen.getByRole('button', { name: 'import' }));

    expect(mocks.importCredential).toHaveBeenCalledWith('codex', 'blob', undefined, undefined);
  });

  it('keeps the dialog open and says so when the import fails', async () => {
    mocks.importCredential.mockRejectedValue(new Error('boom'));
    const { onChanged, onOpenChange } = renderDialog({ status: status({ scriptableLogin: false }) });

    await userEvent.type(screen.getByPlaceholderText('importPlaceholder'), 'blob');
    await userEvent.click(screen.getByRole('button', { name: 'import' }));

    await vi.waitFor(() => expect(mocks.toast.error).toHaveBeenCalledWith('loginFailed'));
    expect(onChanged).not.toHaveBeenCalled();
    expect(onOpenChange).not.toHaveBeenCalled();
  });
});
