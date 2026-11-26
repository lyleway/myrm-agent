/** @vitest-environment jsdom */

import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  isTauriRuntime: vi.fn(),
  listeners: new Map<string, () => void>(),
  unlisten: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: mocks.isTauriRuntime }));
vi.mock('@tauri-apps/api/event', () => ({
  listen: vi.fn(async (event: string, handler: () => void) => {
    mocks.listeners.set(event, handler);
    return mocks.unlisten;
  }),
}));

import { useTrayEvents } from '../useTrayEvents';

describe('useTrayEvents', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listeners.clear();
    mocks.isTauriRuntime.mockReturnValue(true);
  });

  it.each([
    ['tray:new_chat', '/'],
    ['tray:settings', '/settings'],
    ['tray:workspace', '/work'],
    ['tray:apps', '/artifacts'],
  ])('routes %s to %s', async (event, route) => {
    renderHook(() => useTrayEvents());
    await waitFor(() => expect(mocks.listeners.has(event)).toBe(true));

    mocks.listeners.get(event)?.();

    expect(mocks.push).toHaveBeenCalledWith(route);
  });

  it('listens to nothing outside the desktop shell', async () => {
    mocks.isTauriRuntime.mockReturnValue(false);

    renderHook(() => useTrayEvents());
    await new Promise((resolve) => setTimeout(resolve, 0));

    expect(mocks.listeners.size).toBe(0);
  });

  it('stops listening on unmount', async () => {
    const { unmount } = renderHook(() => useTrayEvents());
    await waitFor(() => expect(mocks.listeners.size).toBe(4));

    unmount();

    expect(mocks.unlisten).toHaveBeenCalledTimes(4);
  });
});
