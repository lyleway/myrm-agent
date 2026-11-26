import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiRequest = vi.hoisted(() => vi.fn());
vi.mock('@/lib/api', () => ({ apiRequest }));

import {
  fetchAppActivity,
  fetchAppDetail,
  installApp,
  listInstalledApps,
  proxyAppFetch,
  reportAppActivity,
  saveGrant,
  uninstallApp,
} from '../artifactApps';

const SHA = 'a'.repeat(64);

describe('artifactApps client', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    apiRequest.mockResolvedValue({ marker: true });
  });

  it('lists the installed apps without surfacing a failed read as a toast', async () => {
    await expect(listInstalledApps()).resolves.toEqual({ marker: true });

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps', { silent: true });
  });

  it('installs with PUT and returns the detail the host page needs', async () => {
    await expect(installApp('art-1')).resolves.toEqual({ marker: true });

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps/art-1', { method: 'PUT' });
  });

  it('uninstalls with DELETE and resolves to nothing', async () => {
    await expect(uninstallApp('art-1')).resolves.toBeUndefined();

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps/art-1', { method: 'DELETE' });
  });

  it('reads one app quietly', async () => {
    await fetchAppDetail('art-1');

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps/art-1', { silent: true });
  });

  it('stores one decision per capability as JSON', async () => {
    const body = { decision: 'allow' as const, scope: ['api.weather.com'], version_sha256: SHA };

    await saveGrant('art-1', 'fetch', body);

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps/art-1/grants/fetch', {
      method: 'PUT',
      body: JSON.stringify(body),
    });
  });

  it('sends a page request through the host proxy without a toast on failure', async () => {
    const request = {
      version_sha256: SHA,
      url: 'https://api.weather.com/v1',
      method: 'GET' as const,
      headers: { accept: 'application/json' },
      body: null,
    };

    await proxyAppFetch('art-1', request);

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps/art-1/fetch', {
      method: 'POST',
      body: JSON.stringify(request),
      silent: true,
    });
  });

  it('reports a page-side call next to the version it ran under, outliving the page, and resolves to nothing', async () => {
    const activity = { capability: 'fetch' as const, outcome: 'ok' as const, detail: 'api.weather.com' };

    await expect(reportAppActivity('art-1', SHA, activity)).resolves.toBeUndefined();

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps/art-1/activity', {
      method: 'POST',
      body: JSON.stringify({ version_sha256: SHA, ...activity }),
      keepalive: true,
      silent: true,
    });
  });

  it('asks for the newest entries with an explicit limit', async () => {
    await fetchAppActivity('art-1', 20);

    expect(apiRequest).toHaveBeenCalledWith('/artifact-apps/art-1/activity?limit=20', { silent: true });
  });
});
