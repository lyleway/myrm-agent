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

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
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
  loginStrategy: 'device_code',
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
  });

  it('logs in to the login of this machine when no account is named', async () => {
    renderDialog();

    await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    expect(mocks.stream.mock.calls[0][0]).toEqual({
      command: 'codex',
      backend: 'codex',
      sessionId: expect.any(String),
      account: undefined,
    });
    expect(screen.getByText('loginTitle:codex')).toBeInTheDocument();
  });

  it('logs in to the named account, and says which one in the title', async () => {
    renderDialog({ account: 'work' });

    await vi.waitFor(() => expect(mocks.stream).toHaveBeenCalledTimes(1));
    expect(mocks.stream.mock.calls[0][0]).toMatchObject({ backend: 'codex', account: 'work' });
    expect(screen.getByText('loginTitleAccount:codex/work')).toBeInTheDocument();
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
