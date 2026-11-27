import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ apiRequest: vi.fn() }));

vi.mock('@/lib/api', () => ({ apiRequest: mocks.apiRequest, fetchWithTimeout: vi.fn() }));

import {
  createExternalAgentAccount,
  deleteExternalAgentAccount,
  importExternalAgentCredential,
  listExternalAgentAccounts,
  logoutExternalAgent,
} from '@/services/external-agents';

/** The JSON body of the n-th request. */
const bodyOf = (call = 0): unknown => JSON.parse(mocks.apiRequest.mock.calls[call][1].body);

describe('external agent accounts', () => {
  beforeEach(() => {
    mocks.apiRequest.mockReset();
    mocks.apiRequest.mockResolvedValue({});
  });

  it('lists the accounts the server reports, and none when it reports none', async () => {
    const work = { backend: 'claude', name: 'work', authenticated: true, authStatus: 'authenticated' };
    mocks.apiRequest.mockResolvedValueOnce({ accounts: [work] });

    expect(await listExternalAgentAccounts()).toEqual([work]);
    expect(mocks.apiRequest).toHaveBeenCalledWith('/external-agents/accounts', { silent: true });

    mocks.apiRequest.mockResolvedValueOnce({});
    expect(await listExternalAgentAccounts()).toEqual([]);
  });

  it('makes an account with a request that fails quietly, so the form can say why', async () => {
    await createExternalAgentAccount('claude', 'work');

    const [url, init] = mocks.apiRequest.mock.calls[0];
    expect(url).toBe('/external-agents/accounts');
    expect(init).toMatchObject({ method: 'POST', silent: true });
    expect(bodyOf()).toEqual({ backend: 'claude', name: 'work' });
  });

  it('removes an account by an address that cannot be bent by its name', async () => {
    await deleteExternalAgentAccount('claude', 'a/b?');

    expect(mocks.apiRequest).toHaveBeenCalledWith('/external-agents/accounts/claude/a%2Fb%3F', {
      method: 'DELETE',
      silent: true,
    });
  });

  it('logs out of the named account, or of the login of the machine when none is named', async () => {
    await logoutExternalAgent('claude', 'work');
    await logoutExternalAgent('claude');

    expect(bodyOf(0)).toEqual({ backend: 'claude', account: 'work' });
    expect(bodyOf(1)).toEqual({ backend: 'claude' });
  });

  it('imports a credential into the named account, or into the login of the machine when none is named', async () => {
    await importExternalAgentCredential('claude', 'blob', 'credentials.json', 'work');
    await importExternalAgentCredential('claude', 'blob');

    expect(bodyOf(0)).toEqual({ backend: 'claude', content: 'blob', filename: 'credentials.json', account: 'work' });
    expect(bodyOf(1)).toEqual({ backend: 'claude', content: 'blob' });
  });
});
