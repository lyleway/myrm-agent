/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../useAppLauncher::useAppLauncher
 *
 * [OUTPUT]
 * - Tests: the state is read from the shell before it is shown; toggling adds or removes and reports the outcome; a
 *   browser has no launcher.
 *
 * [POS]
 * The menu never shows a launcher state it has not read, and never claims a change that did not happen.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  isTauriRuntime: vi.fn(),
  hasAppLauncher: vi.fn(),
  createAppLauncher: vi.fn(),
  removeAppLauncher: vi.fn(),
}));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: mocks.isTauriRuntime }));
vi.mock('@/lib/desktop/appLauncher', () => ({
  hasAppLauncher: mocks.hasAppLauncher,
  createAppLauncher: mocks.createAppLauncher,
  removeAppLauncher: mocks.removeAppLauncher,
}));

import { useAppLauncher } from '../useAppLauncher';

describe('useAppLauncher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isTauriRuntime.mockReturnValue(true);
    mocks.hasAppLauncher.mockResolvedValue(false);
    mocks.createAppLauncher.mockResolvedValue(true);
    mocks.removeAppLauncher.mockResolvedValue(true);
  });

  it('knows nothing until the shell has answered', async () => {
    const { result } = renderHook(() => useAppLauncher('art-1', 'Weather'));

    expect(result.current).toMatchObject({ supported: true, present: null });
    await waitFor(() => expect(result.current.present).toBe(false));
  });

  it('adds the launcher under the app’s name when there is none', async () => {
    const { result } = renderHook(() => useAppLauncher('art-1', 'Weather'));
    await waitFor(() => expect(result.current.present).toBe(false));

    let outcome: string | undefined;
    await act(async () => {
      outcome = await result.current.toggle();
    });

    expect(outcome).toBe('added');
    expect(mocks.createAppLauncher).toHaveBeenCalledWith('art-1', 'Weather');
    expect(result.current.present).toBe(true);
  });

  it('removes the launcher when there is one', async () => {
    mocks.hasAppLauncher.mockResolvedValue(true);
    const { result } = renderHook(() => useAppLauncher('art-1', 'Weather'));
    await waitFor(() => expect(result.current.present).toBe(true));

    let outcome: string | undefined;
    await act(async () => {
      outcome = await result.current.toggle();
    });

    expect(outcome).toBe('removed');
    expect(mocks.removeAppLauncher).toHaveBeenCalledWith('art-1');
    expect(result.current.present).toBe(false);
  });

  it('keeps the state it showed when the shell refuses the change', async () => {
    mocks.createAppLauncher.mockResolvedValue(false);
    const { result } = renderHook(() => useAppLauncher('art-1', 'Weather'));
    await waitFor(() => expect(result.current.present).toBe(false));

    let outcome: string | undefined;
    await act(async () => {
      outcome = await result.current.toggle();
    });

    expect(outcome).toBe('failed');
    expect(result.current.present).toBe(false);
  });

  it('has no launcher outside the desktop app and never asks the shell', async () => {
    mocks.isTauriRuntime.mockReturnValue(false);

    const { result } = renderHook(() => useAppLauncher('art-1', 'Weather'));
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(result.current).toMatchObject({ supported: false, present: null });
    expect(mocks.hasAppLauncher).not.toHaveBeenCalled();
  });
});
