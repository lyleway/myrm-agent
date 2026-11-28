/** @vitest-environment jsdom */
import { act, renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import type { MCPServiceConfig } from '@/store/config/types';
import useConfigStore from '@/store/useConfigStore';
import { useMcpServerNameResolver } from '../useMcpServerNameResolver';

const server = (name: string): MCPServiceConfig => ({
  name,
  type: 'streamable_http',
  url: 'https://example.com/mcp',
  description: '',
  enabled: true,
});

beforeEach(() => {
  act(() => useConfigStore.setState({ mcpConfigs: [], orgMcpConfigs: [] }));
});

describe('useMcpServerNameResolver', () => {
  it('names the servers the user configured by the identity the runtime derives', () => {
    act(() => useConfigStore.setState({ mcpConfigs: [server('天气'), server('my-api')] }));

    const { result } = renderHook(() => useMcpServerNameResolver());

    expect(result.current('unnamed_dd4acd')).toBe('天气');
    expect(result.current('my_api')).toBe('my-api');
    expect(result.current('github')).toBeUndefined();
  });

  it('names the servers of the organisation too', () => {
    act(() => useConfigStore.setState({ orgMcpConfigs: [server('我的服务')] }));

    const { result } = renderHook(() => useMcpServerNameResolver());

    expect(result.current('unnamed_13bd86')).toBe('我的服务');
  });

  it('prefers the organisation server when both lists hold one identity, as the runtime does', () => {
    act(() => useConfigStore.setState({ mcpConfigs: [server('my-api')], orgMcpConfigs: [server('my_api')] }));

    const { result } = renderHook(() => useMcpServerNameResolver());

    expect(result.current('my_api')).toBe('my_api');
  });

  it('follows the settings while the component stays mounted', () => {
    const { result } = renderHook(() => useMcpServerNameResolver());
    expect(result.current('unnamed_dd4acd')).toBeUndefined();

    act(() => useConfigStore.setState({ mcpConfigs: [server('天气')] }));

    expect(result.current('unnamed_dd4acd')).toBe('天气');
  });

  it('hands out the same resolver until a list changes', () => {
    act(() => useConfigStore.setState({ mcpConfigs: [server('天气')] }));
    const { result, rerender } = renderHook(() => useMcpServerNameResolver());
    const first = result.current;

    rerender();
    expect(result.current).toBe(first);

    act(() => useConfigStore.setState({ mcpConfigs: [server('天气'), server('github')] }));
    expect(result.current).not.toBe(first);
  });
});
