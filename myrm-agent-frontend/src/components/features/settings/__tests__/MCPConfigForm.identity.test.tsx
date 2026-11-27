/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

import type { MCPServiceConfig } from '@/store/config/types';

const mocks = vi.hoisted(() => ({
  mcpConfig: {
    configs: [] as MCPServiceConfig[],
    mcpOptions: null,
    persistConfigs: vi.fn(),
    setShowImportModal: vi.fn(),
    showImportModal: true,
    deleteConfirmIndex: null,
    pendingRiskAck: null,
    pendingToggleAck: null,
    pendingReloadConfirm: null,
  },
  toast: vi.fn(),
}));

const stableT = (key: string, values?: Record<string, number>) => (values ? `${key}:${JSON.stringify(values)}` : key);

vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('@/store/useConfigStore', () => ({ default: () => ({}) }));
vi.mock('@/hooks/shared/useToast', () => ({ useToast: () => ({ toast: mocks.toast }) }));
vi.mock('@/hooks/settings/useMCPConfig', () => ({ useMCPConfig: () => mocks.mcpConfig }));
vi.mock('@/hooks/settings/useMcpSecurityGate', () => ({
  gateMcpConfigBatch: vi.fn().mockResolvedValue({ blocked: null, needsAcknowledgement: null, scanResults: [] }),
  buildLastScanSummary: vi.fn(),
}));
vi.mock('../mcp/MCPConfigList', () => ({ MCPConfigList: () => null }));
vi.mock('../mcp/MCPConfigEditor', () => ({ MCPConfigEditor: () => null }));
vi.mock('../mcp/DeleteConfirmDialog', () => ({ DeleteConfirmDialog: () => null }));
vi.mock('../mcp/MCPScanAckDialog', () => ({ MCPScanAckDialog: () => null }));
vi.mock('../mcp/MCPReloadConfirmDialog', () => ({ MCPReloadConfirmDialog: () => null }));
vi.mock('../mcp/MCPJsonImporter', () => ({
  MCPJsonImporter: ({
    importJsonText,
    onImportJsonTextChange,
    onImport,
  }: {
    importJsonText: string;
    onImportJsonTextChange: (value: string) => void;
    onImport: () => void;
  }) => (
    <div>
      <textarea aria-label="json" value={importJsonText} onChange={(e) => onImportJsonTextChange(e.target.value)} />
      <button onClick={onImport}>import</button>
    </div>
  ),
}));

import MCPConfigForm from '../MCPConfigForm';

const service = (name: string): MCPServiceConfig => ({
  name,
  type: 'sse',
  url: 'https://mcp.example.com/sse',
  description: 'A service',
  enabled: true,
});

/** An `mcpServers` document with one described SSE server per name (a description keeps imports enabled). */
const serversJson = (...names: string[]): string =>
  JSON.stringify({
    mcpServers: Object.fromEntries(
      names.map((name) => [name, { url: 'https://mcp.example.com/sse', description: 'x' }]),
    ),
  });

/** Opens the import dialog on a form that already holds `configured`, pastes `json` and presses import. */
const importServers = (configured: string[], json: string) => {
  mocks.mcpConfig.configs = configured.map(service);
  render(<MCPConfigForm currentConfigs={mocks.mcpConfig.configs} onSave={vi.fn()} />);
  fireEvent.change(screen.getByLabelText('json'), { target: { value: json } });
  fireEvent.click(screen.getByText('import'));
};

describe('MCPConfigForm JSON 导入按身份判重', () => {
  beforeEach(() => {
    mocks.mcpConfig.persistConfigs.mockClear();
    mocks.toast.mockClear();
  });

  it('跳过与已有服务、与同一批前面的服务同身份的服务，其余按原顺序追加并报告跳过数', async () => {
    importServers(['my_api', '天气'], serversJson('my-api', 'docs', 'team-docs', 'team_docs', '我的服务', '天气'));

    await waitFor(() => expect(mocks.mcpConfig.persistConfigs).toHaveBeenCalledTimes(1));
    const [saved, options] = mocks.mcpConfig.persistConfigs.mock.calls[0] as [
      MCPServiceConfig[],
      { toast: { description: string } },
    ];
    expect(saved.map((config) => config.name)).toEqual(['my_api', '天气', 'docs', 'team-docs', '我的服务']);
    expect(options.toast.description).toBe('mcpImportSuccessDesc:{"count":3} · mcpImportSkipped:{"count":3}');
  });

  it('全部同身份时不写入，只提示跳过数', async () => {
    importServers(['my_api'], serversJson('my-api', 'my api'));

    await waitFor(() => expect(mocks.toast).toHaveBeenCalledTimes(1));
    expect(mocks.toast).toHaveBeenCalledWith({
      title: 'mcpImportSuccess',
      description: 'mcpImportSkipped:{"count":2}',
    });
    expect(mocks.mcpConfig.persistConfigs).not.toHaveBeenCalled();
  });
});
