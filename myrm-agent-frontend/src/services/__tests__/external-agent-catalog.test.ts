import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock('@/lib/api', () => ({ apiRequest: mocks.apiRequest, fetchWithTimeout: vi.fn() }));

import { getExternalAgentCatalog, type ExternalAgentContract } from '@/services/external-agents';

const gemini: ExternalAgentContract = {
  backend: 'gemini',
  title: 'Gemini CLI',
  defaultName: 'gemini-cli',
  description: 'Google Gemini-powered coding agent',
  transport: 'acp',
  mcpNeedsTrustedFolder: true,
  availableInDeployment: true,
};

describe('external agent catalog', () => {
  beforeEach(() => {
    mocks.apiRequest.mockReset();
  });

  it('lists the contracts the server reports, and none when it reports none', async () => {
    mocks.apiRequest.mockResolvedValueOnce({ agents: [gemini] });
    expect(await getExternalAgentCatalog()).toEqual([gemini]);

    mocks.apiRequest.mockResolvedValueOnce({});
    expect(await getExternalAgentCatalog()).toEqual([]);
  });

  it('asks quietly, so a catalog that cannot be read leaves the form without presets and not with an error toast', async () => {
    mocks.apiRequest.mockResolvedValueOnce({ agents: [] });

    await getExternalAgentCatalog();

    expect(mocks.apiRequest).toHaveBeenCalledWith('/external-agents/catalog', { silent: true });
  });
});
