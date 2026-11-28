/** @vitest-environment jsdom */
import { renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { contract, knownContracts } from './knownAgents';

const mocks = vi.hoisted(() => ({ catalog: vi.fn() }));

vi.mock('@/services/external-agents', () => ({ getExternalAgentCatalog: mocks.catalog }));

import { useExternalAgentCatalog } from '../useExternalAgentCatalog';

describe('useExternalAgentCatalog', () => {
  beforeEach(() => {
    mocks.catalog.mockReset();
  });

  it('holds no agent until the server has answered, then the agents it lists', async () => {
    mocks.catalog.mockResolvedValueOnce(knownContracts());
    const { result } = renderHook(() => useExternalAgentCatalog());

    expect(result.current).toEqual([]);
    await waitFor(() => expect(result.current).toEqual(knownContracts()));
  });

  it('asks once, however often the screen renders', async () => {
    mocks.catalog.mockResolvedValue([contract('gemini')]);
    const { result, rerender } = renderHook(() => useExternalAgentCatalog());
    await waitFor(() => expect(result.current).toHaveLength(1));

    rerender();
    rerender();

    expect(mocks.catalog).toHaveBeenCalledTimes(1);
  });

  it('holds no agent when the server cannot answer, and does not fail the screen', async () => {
    mocks.catalog.mockRejectedValueOnce(new Error('offline'));
    const { result } = renderHook(() => useExternalAgentCatalog());

    await waitFor(() => expect(mocks.catalog).toHaveBeenCalledTimes(1));
    await Promise.resolve();

    expect(result.current).toEqual([]);
  });
});
