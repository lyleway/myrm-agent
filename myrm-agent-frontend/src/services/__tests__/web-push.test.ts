/**
 * The test push: a count of reached devices when it reached some, an error carrying the server's reasons when it
 * reached none, so a push that went nowhere is never reported as a success.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('@/lib/api', () => ({ getApiUrl: (path: string) => `http://api.test/api/v1${path}` }));

import { sendTestPush } from '../web-push';

function replyWith(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
}

describe('sendTestPush', () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
  });

  afterEach(() => {
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  it('posts to the test endpoint and resolves with the number of devices reached', async () => {
    fetchMock.mockResolvedValue(replyWith({ delivered: 2, subscriptions: 2, failures: [] }));

    await expect(sendTestPush()).resolves.toBe(2);

    expect(fetchMock).toHaveBeenCalledWith(
      'http://api.test/api/v1/web-push/test',
      expect.objectContaining({ method: 'POST' }),
    );
  });

  it('still resolves when only some devices were missed', async () => {
    fetchMock.mockResolvedValue(
      replyWith({ delivered: 1, subscriptions: 2, failures: ['HTTP 410 Gone (subscription removed)'] }),
    );

    await expect(sendTestPush()).resolves.toBe(1);
  });

  it('rejects with every distinct reason when no device was reached', async () => {
    fetchMock.mockResolvedValue(
      replyWith({ delivered: 0, subscriptions: 2, failures: ['HTTP 403 Forbidden', 'ConnectTimeout'] }),
    );

    await expect(sendTestPush()).rejects.toThrow('HTTP 403 Forbidden; ConnectTimeout');
  });

  it('rejects with the reason the server gives when no device is registered', async () => {
    fetchMock.mockResolvedValue(
      replyWith({ delivered: 0, subscriptions: 0, failures: ['No device is registered for Web Push on this server.'] }),
    );

    await expect(sendTestPush()).rejects.toThrow('No device is registered for Web Push on this server.');
  });

  it('rejects with the status when the request itself failed', async () => {
    fetchMock.mockResolvedValue(replyWith({ detail: 'boom' }, 500));

    await expect(sendTestPush()).rejects.toThrow('Failed to send test push: 500');
  });
});
