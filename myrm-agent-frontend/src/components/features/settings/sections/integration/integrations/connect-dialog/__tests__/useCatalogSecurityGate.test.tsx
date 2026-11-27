/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, renderHook } from '@testing-library/react';

import type { MCPServiceConfig } from '@/store/config/types';
import {
  GATE_ALLOWED,
  makeStoredConfig,
  mockGateMcpEnable,
  mockSetMCPConfigs,
  mockToast,
  resetConnectDialogMocks,
} from './connectDialogTestKit';

vi.mock('next-intl', async () => (await import('./connectDialogTestKit')).nextIntlMock);
vi.mock('@/hooks/shared/useToast', async () => (await import('./connectDialogTestKit')).toastMock);
vi.mock('@/hooks/settings/useMcpSecurityGate', async () => (await import('./connectDialogTestKit')).securityGateMock);
vi.mock('@/lib/utils/mcp-config', async () => (await import('./connectDialogTestKit')).mcpConfigUtilsMock);

import { useCatalogSecurityGate } from '../useCatalogSecurityGate';

describe('useCatalogSecurityGate', () => {
  beforeEach(resetConnectDialogMocks);

  // A copy saved while the acknowledgement was open keeps the new service out when both would answer to one tool prefix.
  it.each([
    ['the same name', 'svc', 'svc'],
    ['a name that differs only in symbols or spaces', 'my-svc', 'my svc'],
  ])(
    'does not store a second copy of a service that was added meanwhile under %s',
    async (_label, connectedName, addedName) => {
      mockGateMcpEnable.mockResolvedValueOnce({ ...GATE_ALLOWED, needsAcknowledgement: true, allowed: false });
      const onConnected = vi.fn();
      const { result, rerender } = renderHook(
        ({ mcpConfigs }: { mcpConfigs: MCPServiceConfig[] }) =>
          useCatalogSecurityGate({
            entryName: 'Svc',
            catalogMcpConfig: null,
            mcpConfigs,
            setMCPConfigs: mockSetMCPConfigs,
            onConnected,
          }),
        { initialProps: { mcpConfigs: [] as MCPServiceConfig[] } },
      );

      await act(async () => {
        await result.current.runCatalogSecurityGate(makeStoredConfig(connectedName));
      });
      expect(result.current.pendingAck).not.toBeNull();

      rerender({ mcpConfigs: [makeStoredConfig(addedName)] });
      await act(async () => {
        await result.current.confirmAcknowledgement();
      });

      expect(mockSetMCPConfigs).not.toHaveBeenCalled();
      expect(mockToast).toHaveBeenCalledExactlyOnceWith({ title: 'connectSuccess' });
      expect(onConnected).toHaveBeenCalledTimes(1);
    },
  );
});
