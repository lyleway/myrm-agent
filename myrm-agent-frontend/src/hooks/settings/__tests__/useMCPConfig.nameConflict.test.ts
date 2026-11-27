import { act, renderHook } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';

import { DEFAULT_FORM_DATA, useMCPConfig } from '@/hooks/settings/useMCPConfig';
import type { MCPServiceConfig } from '@/store/config/types';

const mocks = vi.hoisted(() => ({ validateMCPConfig: vi.fn() }));

const stableT = (key: string, values?: Record<string, string>) => (values ? `${key}:${JSON.stringify(values)}` : key);

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('@/hooks/shared/useToast', () => ({ useToast: () => ({ toast: vi.fn() }) }));
vi.mock('@/store/useConfigStore', () => ({ default: () => ({ validateMCPConfig: mocks.validateMCPConfig }) }));
vi.mock('@/services/llm-config', () => ({
  getMCPOptions: vi.fn().mockResolvedValue(null),
  scanMCPConfig: vi.fn(),
  scanMCPConfigBatch: vi.fn(),
  validateMCPConfig: vi.fn(),
}));

const service = (name: string): MCPServiceConfig => ({
  ...DEFAULT_FORM_DATA,
  name,
  type: 'sse',
  url: 'https://mcp.example.com/sse',
  description: 'A service',
  enabled: false,
});

// One reference for the whole file: the hook re-reads its configs whenever the array it is given changes.
const CONFIGS = [service('my_api'), service('github'), service('天气')];

/** Fills the form the way the editor does (`editingIndex` is null for a new service) and validates it. */
const validate = async (editingIndex: number | null, name: string) => {
  const { result } = renderHook(() => useMCPConfig(CONFIGS, vi.fn()));
  await act(async () => {});
  act(() => {
    result.current.setEditingIndex(editingIndex);
    result.current.setFormData(service(name));
  });
  let valid = false;
  act(() => {
    valid = result.current.validateForm();
  });
  return { valid, nameError: result.current.errors.name };
};

describe('useMCPConfig 名称判重', () => {
  it('新增时，与已有服务同名或只差符号、空格的名字被拒，并指明已有的那一个', async () => {
    expect(await validate(null, 'my_api')).toEqual({ valid: false, nameError: 'mcpNameConflict:{"name":"my_api"}' });
    expect(await validate(null, 'my-api')).toEqual({ valid: false, nameError: 'mcpNameConflict:{"name":"my_api"}' });
    expect(await validate(null, 'my api')).toEqual({ valid: false, nameError: 'mcpNameConflict:{"name":"my_api"}' });
  });

  it('编辑时，保留自己的名字不算冲突', async () => {
    expect(await validate(0, 'my_api')).toEqual({ valid: true, nameError: undefined });
    expect(await validate(0, 'my-api')).toEqual({ valid: true, nameError: undefined });
  });

  it('编辑时，改成另一个服务的名字算冲突', async () => {
    expect(await validate(1, 'my-api')).toEqual({ valid: false, nameError: 'mcpNameConflict:{"name":"my_api"}' });
  });

  it('不同名字、大小写不同的名字、不同的中文名字都不冲突', async () => {
    expect(await validate(null, 'docs')).toEqual({ valid: true, nameError: undefined });
    expect(await validate(null, 'GitHub')).toEqual({ valid: true, nameError: undefined });
    expect(await validate(null, '我的服务')).toEqual({ valid: true, nameError: undefined });
  });

  it('同一个中文名字算冲突', async () => {
    expect(await validate(null, '天气')).toEqual({ valid: false, nameError: 'mcpNameConflict:{"name":"天气"}' });
  });

  it('名字为空时报名称必填，不报冲突', async () => {
    expect(await validate(null, '  ')).toEqual({ valid: false, nameError: 'mcpNameRequired' });
  });
});
