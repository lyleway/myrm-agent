/** @vitest-environment jsdom */
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAuthStatus, ExternalAgentTerminalLogin } from '@/services/external-agents';

const stableT = (key: string, values?: Record<string, string>) =>
  values ? `${key}:${Object.values(values).join('/')}` : key;

const mocks = vi.hoisted(() => ({
  terminalLogin: vi.fn(),
  writeToClipboard: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('@/lib/utils/clipboardUtils', () => ({ writeToClipboard: mocks.writeToClipboard }));
vi.mock('@/services/external-agents', () => ({ getExternalAgentTerminalLogin: mocks.terminalLogin }));

import ExternalAgentTerminalLoginDialog from '../ExternalAgentTerminalLoginDialog';
import { agentRow } from './knownAgents';

const RECHECK_MS = 4000;
const CLOSE_MS = 1500;

const status = (overrides: Partial<ExternalAgentAuthStatus> = {}): ExternalAgentAuthStatus =>
  agentRow('claude', { path: '/usr/local/bin/claude', version: '2.1.0', ...overrides });

const answer = (overrides: Partial<ExternalAgentTerminalLogin> = {}): ExternalAgentTerminalLogin => ({
  backend: 'claude',
  account: null,
  authenticated: false,
  authStatus: 'not_authenticated',
  defaultShell: 'posix',
  commands: { posix: 'posix-command', powershell: 'powershell-command', cmd: 'cmd-command' },
  ...overrides,
});

const deferred = <T,>() => {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
};

const renderDialog = (overrides: Partial<React.ComponentProps<typeof ExternalAgentTerminalLoginDialog>> = {}) => {
  const props = { open: true, onOpenChange: vi.fn(), status: status(), onChanged: vi.fn(), ...overrides };
  const view = render(<ExternalAgentTerminalLoginDialog {...props} />);
  return {
    ...props,
    rerender: (next: Partial<typeof props>) => view.rerender(<ExternalAgentTerminalLoginDialog {...props} {...next} />),
  };
};

describe('ExternalAgentTerminalLoginDialog', () => {
  beforeEach(() => {
    mocks.terminalLogin.mockReset();
    mocks.terminalLogin.mockResolvedValue(answer());
    mocks.writeToClipboard.mockReset();
    mocks.writeToClipboard.mockResolvedValue(true);
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('shows the command of the login of this machine, in the shell of this machine, when no account is named', async () => {
    renderDialog();

    expect(await screen.findByText('posix-command')).toBeInTheDocument();
    expect(mocks.terminalLogin).toHaveBeenCalledWith('claude', undefined);
    expect(screen.getByText('loginTitle:claude')).toBeInTheDocument();
  });

  it('asks for the named account and says which one in the title', async () => {
    renderDialog({ account: 'work' });

    expect(await screen.findByText('posix-command')).toBeInTheDocument();
    expect(mocks.terminalLogin).toHaveBeenCalledWith('claude', 'work');
    expect(screen.getByText('loginTitleAccount:claude/work')).toBeInTheDocument();
  });

  it('starts from the shell of the host, even when it is not the first one', async () => {
    mocks.terminalLogin.mockResolvedValue(answer({ defaultShell: 'powershell' }));
    renderDialog();

    expect(await screen.findByText('powershell-command')).toBeInTheDocument();
    expect(screen.getByRole('radio', { name: 'shellPowershell' })).toBeChecked();
  });

  it('lets the user switch to the shell they are in', async () => {
    renderDialog();
    await screen.findByText('posix-command');

    await userEvent.click(screen.getByRole('radio', { name: 'shellCmd' }));

    expect(screen.getByText('cmd-command')).toBeInTheDocument();
    expect(screen.queryByText('posix-command')).not.toBeInTheDocument();
  });

  it('offers only the shells that can spell the command', async () => {
    mocks.terminalLogin.mockResolvedValue(
      answer({ commands: { posix: 'posix-command', powershell: 'powershell-command' } }),
    );
    renderDialog();
    await screen.findByText('posix-command');

    expect(screen.getByRole('radio', { name: 'shellPowershell' })).toBeInTheDocument();
    expect(screen.queryByRole('radio', { name: 'shellCmd' })).not.toBeInTheDocument();
  });

  it('shows no shell choice when only one shell can spell the command', async () => {
    mocks.terminalLogin.mockResolvedValue(answer({ commands: { posix: 'posix-command' } }));
    renderDialog();
    await screen.findByText('posix-command');

    expect(screen.queryByRole('radio')).not.toBeInTheDocument();
  });

  it('falls back to a shell that can spell the command when the one of the host cannot', async () => {
    mocks.terminalLogin.mockResolvedValue(
      answer({ defaultShell: 'cmd', commands: { powershell: 'powershell-command' } }),
    );
    renderDialog();

    expect(await screen.findByText('powershell-command')).toBeInTheDocument();
  });

  it('copies the command that is shown', async () => {
    renderDialog();
    await screen.findByText('posix-command');

    await userEvent.click(screen.getByRole('button', { name: 'terminalLoginCopy' }));

    expect(mocks.writeToClipboard).toHaveBeenCalledWith('posix-command', true);
    expect(await screen.findByRole('button', { name: 'terminalLoginCopied' })).toBeInTheDocument();
  });

  describe('the note that a window keeps using the account', () => {
    it('is given for a shell whose settings last for the whole window', async () => {
      renderDialog({ account: 'work' });
      await screen.findByText('posix-command');
      expect(screen.queryByText('terminalLoginWindowNote')).not.toBeInTheDocument();

      await userEvent.click(screen.getByRole('radio', { name: 'shellPowershell' }));

      expect(screen.getByText('terminalLoginWindowNote')).toBeInTheDocument();
    });

    it('is not given when no account is named, since the window keeps nothing then', async () => {
      renderDialog();
      await screen.findByText('posix-command');

      await userEvent.click(screen.getByRole('radio', { name: 'shellPowershell' }));

      expect(screen.queryByText('terminalLoginWindowNote')).not.toBeInTheDocument();
    });
  });

  describe('waiting for the login in the terminal', () => {
    beforeEach(() => {
      vi.useFakeTimers({ shouldAdvanceTime: true });
    });

    const advance = (ms: number) => act(async () => vi.advanceTimersByTimeAsync(ms));

    it('asks again by itself, and keeps the shell the user picked while it does', async () => {
      renderDialog();
      await screen.findByText('posix-command');
      expect(screen.getByText('terminalLoginWaiting')).toBeInTheDocument();
      await userEvent.click(screen.getByRole('radio', { name: 'shellCmd' }));

      await advance(RECHECK_MS);

      expect(mocks.terminalLogin).toHaveBeenCalledTimes(2);
      expect(screen.getByText('cmd-command')).toBeInTheDocument();
    });

    it('reports the login once the CLI says it is made, and closes after a moment', async () => {
      mocks.terminalLogin.mockResolvedValueOnce(answer()).mockResolvedValue(answer({ authenticated: true }));
      const { onChanged, onOpenChange } = renderDialog({ account: 'work' });
      await screen.findByText('posix-command');
      expect(onChanged).not.toHaveBeenCalled();

      await advance(RECHECK_MS);

      expect(mocks.toast.success).toHaveBeenCalledWith('loginSuccess');
      expect(onChanged).toHaveBeenCalledTimes(1);
      expect(onOpenChange).not.toHaveBeenCalled();

      await advance(CLOSE_MS);

      expect(onOpenChange).toHaveBeenCalledWith(false);
    });

    it('stops asking once the login is made', async () => {
      mocks.terminalLogin.mockResolvedValue(answer({ authenticated: true }));
      renderDialog();
      await screen.findByText('loginSuccess');

      await advance(RECHECK_MS * 3);

      expect(mocks.terminalLogin).toHaveBeenCalledTimes(1);
    });

    it('asks at once when the user asks to check again', async () => {
      renderDialog();
      await screen.findByText('posix-command');

      await userEvent.click(screen.getByRole('button', { name: 'terminalLoginCheck' }));

      await vi.waitFor(() => expect(mocks.terminalLogin).toHaveBeenCalledTimes(2));
    });

    it('stops asking when the dialog is closed', async () => {
      const { rerender } = renderDialog();
      await screen.findByText('posix-command');

      rerender({ open: false });
      await advance(RECHECK_MS * 3);

      expect(mocks.terminalLogin).toHaveBeenCalledTimes(1);
    });

    it('does not ask at all while the dialog is closed', async () => {
      renderDialog({ open: false });

      await advance(RECHECK_MS * 2);

      expect(mocks.terminalLogin).not.toHaveBeenCalled();
    });

    it('does not start a second round of asking when its parent renders again', async () => {
      const onChanged = vi.fn();
      const { rerender } = renderDialog({ onChanged });
      await screen.findByText('posix-command');

      rerender({ onChanged: vi.fn(), onOpenChange: vi.fn() });
      await advance(RECHECK_MS);

      expect(mocks.terminalLogin).toHaveBeenCalledTimes(2);
    });

    it('does nothing with an answer that arrives after the dialog was closed', async () => {
      const late = deferred<ExternalAgentTerminalLogin>();
      mocks.terminalLogin.mockReturnValueOnce(late.promise);
      const { rerender, onChanged } = renderDialog();
      await vi.waitFor(() => expect(mocks.terminalLogin).toHaveBeenCalledTimes(1));

      rerender({ open: false });
      await act(async () => late.resolve(answer({ authenticated: true })));

      expect(mocks.toast.success).not.toHaveBeenCalled();
      expect(onChanged).not.toHaveBeenCalled();
    });

    it('shows a check as under way only when the user asked for it', async () => {
      renderDialog();
      await screen.findByText('posix-command');
      const check = () => screen.getByRole('button', { name: 'terminalLoginCheck' });

      const byItself = deferred<ExternalAgentTerminalLogin>();
      mocks.terminalLogin.mockReturnValueOnce(byItself.promise);
      await advance(RECHECK_MS);
      expect(mocks.terminalLogin).toHaveBeenCalledTimes(2);
      expect(check()).toBeEnabled();
      await act(async () => byItself.resolve(answer()));

      const asked = deferred<ExternalAgentTerminalLogin>();
      mocks.terminalLogin.mockReturnValueOnce(asked.promise);
      await userEvent.click(check());
      expect(check()).toBeDisabled();
      await act(async () => asked.resolve(answer()));
      expect(check()).toBeEnabled();
    });

    it('says so, with a way to try again, and does not keep asking when the command cannot be had', async () => {
      mocks.terminalLogin.mockRejectedValue(new Error('409'));
      renderDialog();

      expect(await screen.findByText('terminalLoginLoadFailed:claude')).toBeInTheDocument();
      await advance(RECHECK_MS * 3);
      expect(mocks.terminalLogin).toHaveBeenCalledTimes(1);

      mocks.terminalLogin.mockResolvedValue(answer());
      await userEvent.click(screen.getByRole('button', { name: 'loginRetry' }));

      expect(await screen.findByText('posix-command')).toBeInTheDocument();
      expect(screen.queryByText('terminalLoginLoadFailed:claude')).not.toBeInTheDocument();
    });
  });
});
