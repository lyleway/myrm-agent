/** @vitest-environment jsdom */
import type { ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import type { MCPServiceConfig } from '@/store/config/types';

const mocks = vi.hoisted(() => ({
  store: {
    mcpConfigs: [] as MCPServiceConfig[],
    orgMcpConfigs: [] as MCPServiceConfig[],
    setMCPConfigs: vi.fn(),
    addMCPConfig: vi.fn(),
    initConfig: vi.fn(),
  },
  toast: vi.fn(),
  /** The server the install wizard stub hands to `onInstall`. */
  offered: null as MCPServiceConfig | null,
}));

const stableT = (key: string, values?: Record<string, string>) => (values ? `${key}:${JSON.stringify(values)}` : key);

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('@/store/useConfigStore', () => ({ default: () => mocks.store }));
vi.mock('@/hooks/shared/useToast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('@/hooks/settings/useMcpSecurityGate', () => ({
  gateMcpConfigBatch: vi.fn().mockResolvedValue({ blocked: null, needsAcknowledgement: null, scanResults: [] }),
  buildLastScanSummary: vi.fn(),
}));
vi.mock('../MCPConfigForm', () => ({ default: () => null }));
vi.mock('../mcp/MCPRegistryBrowser', () => ({
  MCPRegistryBrowser: ({ onSelectInstall }: { onSelectInstall: (qualifiedName: string) => void }) => (
    <button onClick={() => onSelectInstall('acme/weather')}>pick</button>
  ),
}));
vi.mock('../mcp/MCPInstallWizard', () => ({
  MCPInstallWizard: ({ onInstall }: { onInstall: (config: MCPServiceConfig) => void }) => (
    <button onClick={() => onInstall(mocks.offered as MCPServiceConfig)}>install</button>
  ),
}));
vi.mock('../sections/SettingsSection', () => ({
  default: ({ children }: { children: ReactNode }) => <div>{children}</div>,
}));

import MCPSection from '../sections/ai-tools/MCPSection';

const service = (name: string): MCPServiceConfig => ({
  name,
  type: 'sse',
  url: 'https://mcp.example.com/sse',
  description: 'A service',
  enabled: true,
});

/** Installs `offered` from the registry tab while `installed` are configured; resolves to the config the store got. */
const installFromRegistry = async (installed: string[], offered: string): Promise<MCPServiceConfig> => {
  mocks.store.mcpConfigs = installed.map(service);
  mocks.offered = service(offered);
  render(<MCPSection />);
  fireEvent.click(screen.getByText('mcpTabRegistry'));
  fireEvent.click(screen.getByText('pick'));
  fireEvent.click(screen.getByText('install'));
  await waitFor(() => expect(mocks.store.addMCPConfig).toHaveBeenCalledTimes(1));
  return mocks.store.addMCPConfig.mock.calls[0][0] as MCPServiceConfig;
};

describe('MCPSection 注册表安装的命名', () => {
  beforeEach(() => {
    mocks.store.addMCPConfig.mockClear();
    mocks.toast.mockClear();
  });

  it.each([
    { installed: ['my_api'], offered: 'my-api', expected: 'my-api-2', why: '只差符号的名字占着同一个身份，要编号' },
    { installed: ['my_api', 'my-api-2'], offered: 'my-api', expected: 'my-api-3', why: '编号也按身份判断是否被占' },
    { installed: ['github'], offered: 'docs', expected: 'docs', why: '没有冲突的名字原样保留' },
    { installed: ['天气'], offered: '我的服务', expected: '我的服务', why: '不同的中文名字是不同的身份' },
  ])('$why：已有 $installed，安装 $offered 得到 $expected', async ({ installed, offered, expected }) => {
    const added = await installFromRegistry(installed, offered);

    expect(added.name).toBe(expected);
    expect(mocks.toast).toHaveBeenCalledWith(
      expect.objectContaining({ description: `mcpRegistryInstallSuccessDesc:${JSON.stringify({ name: expected })}` }),
    );
  });
});
