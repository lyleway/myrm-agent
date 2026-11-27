/** @vitest-environment jsdom */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { ExternalAgentAccount } from '@/services/external-agents';

const mocks = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock('@/services/external-agents', () => ({ listExternalAgentAccounts: mocks.list }));

import { useExternalAgentAccounts } from '../useExternalAgentAccounts';

const account = (name: string, authenticated: boolean): ExternalAgentAccount => ({
  backend: 'claude',
  name,
  authenticated,
  authStatus: authenticated ? 'authenticated' : 'not_authenticated',
});

describe('useExternalAgentAccounts', () => {
  beforeEach(() => {
    mocks.list.mockReset();
  });

  it('holds no accounts and asks nobody until it is told to read', () => {
    const { result } = renderHook(() => useExternalAgentAccounts());

    expect(result.current.accounts).toEqual([]);
    expect(mocks.list).not.toHaveBeenCalled();
  });

  it('holds what the server reports, and the latest report on every later read', async () => {
    mocks.list.mockResolvedValueOnce([account('work', false)]).mockResolvedValueOnce([account('work', true)]);
    const { result } = renderHook(() => useExternalAgentAccounts());

    await act(() => result.current.refreshAccounts());
    expect(result.current.accounts).toEqual([account('work', false)]);

    await act(() => result.current.refreshAccounts());
    expect(result.current.accounts).toEqual([account('work', true)]);
  });

  it('keeps the last list when a read fails', async () => {
    mocks.list.mockResolvedValueOnce([account('work', true)]).mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(() => useExternalAgentAccounts());

    await act(() => result.current.refreshAccounts());
    await act(() => result.current.refreshAccounts());

    expect(result.current.accounts).toEqual([account('work', true)]);
  });

  it('hands out the same reader on every render, so an effect that depends on it runs once', () => {
    const { result, rerender } = renderHook(() => useExternalAgentAccounts());
    const first = result.current.refreshAccounts;

    rerender();

    expect(result.current.refreshAccounts).toBe(first);
  });
});
