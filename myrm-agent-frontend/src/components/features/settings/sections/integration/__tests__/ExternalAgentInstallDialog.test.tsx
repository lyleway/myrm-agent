/** @vitest-environment jsdom */
import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAuthEvent } from '@/services/external-agents';

const stableT = (key: string, values?: Record<string, string>) => (values ? `${key}:${values.backend}` : key);

const mocks = vi.hoisted(() => ({
  install: vi.fn(),
  toast: { success: vi.fn(), error: vi.fn() },
}));

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('@/services/external-agents', () => ({ streamExternalAgentInstall: mocks.install }));

import ExternalAgentInstallDialog from '../ExternalAgentInstallDialog';

/** An install stream scripted by the test: push events, then end or fail it. */
function scriptedStream() {
  const queue: ExternalAgentAuthEvent[] = [];
  let wake: (() => void) | null = null;
  let finished = false;
  let failure: Error | null = null;
  let signal: AbortSignal | undefined;

  async function* stream(_backend: string, abortSignal?: AbortSignal): AsyncGenerator<ExternalAgentAuthEvent> {
    signal = abortSignal;
    for (;;) {
      if (queue.length) {
        yield queue.shift() as ExternalAgentAuthEvent;
      } else if (failure) {
        throw failure;
      } else if (finished) {
        return;
      } else {
        await new Promise<void>((resolve) => {
          wake = resolve;
        });
      }
    }
  }
  const poke = () => wake?.();
  return {
    stream,
    push: (event: ExternalAgentAuthEvent) => {
      queue.push(event);
      poke();
    },
    end: () => {
      finished = true;
      poke();
    },
    fail: (error: Error) => {
      failure = error;
      poke();
    },
    get signal() {
      return signal;
    },
  };
}

const renderDialog = (overrides: Partial<React.ComponentProps<typeof ExternalAgentInstallDialog>> = {}) => {
  const props = { open: true, onOpenChange: vi.fn(), backend: 'claude', onInstalled: vi.fn(), ...overrides };
  const view = render(<ExternalAgentInstallDialog {...props} />);
  return { ...props, ...view };
};

describe('ExternalAgentInstallDialog', () => {
  beforeEach(() => {
    mocks.install.mockReset();
    mocks.toast.success.mockClear();
    mocks.toast.error.mockClear();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it('starts the install as soon as it opens and shows the installer log', async () => {
    const script = scriptedStream();
    mocks.install.mockImplementation(script.stream);

    renderDialog();
    script.push({ type: 'progress', message: 'Downloading Node.js' });

    expect(await screen.findByText('Downloading Node.js')).toBeInTheDocument();
    expect(screen.getByText('installInProgress')).toBeInTheDocument();
    expect(mocks.install).toHaveBeenCalledTimes(1);
    expect(mocks.install.mock.calls[0][0]).toBe('claude');
  });

  it('does not start a second install when the parent re-renders mid-install', async () => {
    const script = scriptedStream();
    mocks.install.mockImplementation(script.stream);
    const view = renderDialog();
    script.push({ type: 'progress', message: 'working' });
    await screen.findByText('working');

    view.rerender(
      <ExternalAgentInstallDialog open onOpenChange={vi.fn()} backend="claude" onInstalled={vi.fn()} />,
    );

    expect(mocks.install).toHaveBeenCalledTimes(1);
    expect(script.signal?.aborted).toBe(false);
  });

  it('refreshes the agent state on success, then closes itself after a short pause', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const script = scriptedStream();
    mocks.install.mockImplementation(script.stream);
    const { onInstalled, onOpenChange } = renderDialog();

    script.push({ type: 'success', message: 'Installation complete' });
    script.end();

    expect(await screen.findByText('Installation complete')).toBeInTheDocument();
    expect(mocks.toast.success).toHaveBeenCalledWith('installSuccess');
    expect(onInstalled).toHaveBeenCalledTimes(1);
    expect(onOpenChange).not.toHaveBeenCalled();

    await act(async () => {
      await vi.advanceTimersByTimeAsync(1600);
    });
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('keeps the refresh when the user closes during the success pause', async () => {
    const script = scriptedStream();
    mocks.install.mockImplementation(script.stream);
    const { onInstalled, onOpenChange } = renderDialog();
    script.push({ type: 'success', message: 'Installation complete' });
    script.end();
    await screen.findByText('Installation complete');

    await userEvent.click(await screen.findByRole('button', { name: 'installClose' }));

    expect(onInstalled).toHaveBeenCalledTimes(1);
    expect(onOpenChange).toHaveBeenCalledWith(false);
  });

  it('offers retry after a failed install and restarts the install', async () => {
    const first = scriptedStream();
    const second = scriptedStream();
    mocks.install.mockImplementationOnce(first.stream).mockImplementationOnce(second.stream);
    const { onInstalled } = renderDialog();
    first.push({ type: 'error', message: 'npm exited with code 1' });
    first.end();

    expect(await screen.findByText('npm exited with code 1')).toBeInTheDocument();
    expect(mocks.toast.error).toHaveBeenCalledWith('npm exited with code 1');
    expect(onInstalled).not.toHaveBeenCalled();

    await userEvent.click(await screen.findByRole('button', { name: 'installRetry' }));
    second.push({ type: 'progress', message: 'second attempt' });

    expect(await screen.findByText('second attempt')).toBeInTheDocument();
    expect(mocks.install).toHaveBeenCalledTimes(2);
    expect(screen.queryByText('npm exited with code 1')).not.toBeInTheDocument();
  });

  it('reports a dropped connection as a failed install', async () => {
    const script = scriptedStream();
    mocks.install.mockImplementation(script.stream);
    const { onInstalled } = renderDialog();

    script.fail(new Error('Install stream failed: 500'));

    expect(await screen.findByText('installFailed')).toBeInTheDocument();
    expect(mocks.toast.error).toHaveBeenCalledWith('installFailed');
    expect(onInstalled).not.toHaveBeenCalled();
    expect(await screen.findByRole('button', { name: 'installRetry' })).toBeInTheDocument();
  });

  it('cancels a running install on the server by aborting the request when the user cancels', async () => {
    const script = scriptedStream();
    mocks.install.mockImplementation(script.stream);
    const { onOpenChange } = renderDialog();
    script.push({ type: 'progress', message: 'working' });
    await screen.findByText('working');

    await userEvent.click(screen.getByRole('button', { name: 'cancel' }));

    expect(script.signal?.aborted).toBe(true);
    expect(onOpenChange).toHaveBeenCalledWith(false);
    expect(mocks.toast.error).not.toHaveBeenCalled();
  });

  it('aborts a running install when the dialog is removed', async () => {
    const script = scriptedStream();
    mocks.install.mockImplementation(script.stream);
    const view = renderDialog();
    script.push({ type: 'progress', message: 'working' });
    await screen.findByText('working');

    view.unmount();

    expect(script.signal?.aborted).toBe(true);
  });

  it('does nothing while closed', () => {
    renderDialog({ open: false });

    expect(mocks.install).not.toHaveBeenCalled();
  });
});
