/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../useAppBridge::useAppBridge
 *
 * [OUTPUT]
 * - Tests: only the window of the embedded iframe is answered; its requests reach the notification and the fetch proxy.
 *
 * [POS]
 * Identity is the security boundary of the channel: another frame, extension or tab must not be able to spend the
 * permissions the user gave this app.
 */

import { render, waitFor } from '@testing-library/react';
import { useRef } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { CapabilityStatus } from '@/lib/app-bridge/declaration';
import type { BridgeCapability } from '@/lib/app-bridge/protocol';

const mocks = vi.hoisted(() => ({
  showAppNotification: vi.fn(),
  proxyAppFetch: vi.fn(),
  reportAppActivity: vi.fn(),
}));

vi.mock('@/services/appNotification', () => ({ showAppNotification: mocks.showAppNotification }));
vi.mock('@/services/artifactApps', () => ({
  proxyAppFetch: mocks.proxyAppFetch,
  reportAppActivity: mocks.reportAppActivity,
}));

import { useAppBridge } from '../useAppBridge';

const ALLOWED: Record<BridgeCapability, CapabilityStatus> = { notify: 'allowed', fetch: 'allowed' };

function Harness({ statuses = ALLOWED }: { statuses?: Record<BridgeCapability, CapabilityStatus> }) {
  const ref = useRef<HTMLDivElement>(null);
  useAppBridge({ artifactId: 'art-1', appName: 'Weather', versionSha256: 'a'.repeat(64), statuses, frameHostRef: ref });
  return (
    <div ref={ref}>
      <iframe title="app" />
    </div>
  );
}

function setup(statuses?: Record<BridgeCapability, CapabilityStatus>) {
  const view = render(<Harness statuses={statuses} />);
  const frame = view.container.querySelector('iframe');
  if (!frame?.contentWindow) {
    throw new Error('the iframe has no window');
  }
  const frameWindow = frame.contentWindow;
  const replies = vi.spyOn(frameWindow, 'postMessage').mockImplementation(() => undefined);
  const send = (data: unknown, source: MessageEventSource | null = frameWindow) =>
    window.dispatchEvent(new MessageEvent('message', { data, source }));
  return { ...view, frameWindow, replies, send };
}

const notify = { jsonrpc: '2.0', id: 1, method: 'notify', params: { title: 'Rain soon', body: 'In 10 minutes' } };

describe('useAppBridge', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.showAppNotification.mockResolvedValue(undefined);
    mocks.reportAppActivity.mockResolvedValue(undefined);
  });

  it('answers a request from the iframe window, posting the reply back to it', async () => {
    const { send, replies } = setup();

    send(notify);

    await waitFor(() => expect(replies).toHaveBeenCalledWith({ jsonrpc: '2.0', id: 1, result: null }, '*'));
    expect(mocks.showAppNotification).toHaveBeenCalledWith('Weather · Rain soon', 'In 10 minutes');
  });

  it('ignores a request from any other window, including the host page itself', async () => {
    const { send, replies } = setup();

    send(notify, window);
    send(notify, null);
    send(notify, document.createElement('iframe').contentWindow);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(replies).not.toHaveBeenCalled();
    expect(mocks.showAppNotification).not.toHaveBeenCalled();
  });

  it('stays silent for messages that are not JSON-RPC requests', async () => {
    const { send, replies } = setup();

    send({ type: 'widget-resize', height: 300 });
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(replies).not.toHaveBeenCalled();
  });

  it('sends a granted fetch through the proxy with the version the host is showing', async () => {
    mocks.proxyAppFetch.mockResolvedValue({
      ok: true,
      status: 200,
      headers: {},
      body: '{"t":1}',
      code: null,
      message: null,
    });
    const { send, replies } = setup();

    send({ jsonrpc: '2.0', id: 'f1', method: 'fetch', params: { url: 'https://api.weather.com/v1/now' } });

    await waitFor(() =>
      expect(replies).toHaveBeenCalledWith(
        { jsonrpc: '2.0', id: 'f1', result: { status: 200, headers: {}, body: '{"t":1}' } },
        '*',
      ),
    );
    expect(mocks.proxyAppFetch).toHaveBeenCalledWith('art-1', {
      url: 'https://api.weather.com/v1/now',
      method: 'GET',
      headers: {},
      body: null,
      version_sha256: 'a'.repeat(64),
    });
  });

  it('refuses while the capability is undecided and logs the refusal for the user', async () => {
    const { send, replies } = setup({ notify: 'ask', fetch: 'ask' });

    send(notify);

    await waitFor(() => expect(replies).toHaveBeenCalled());
    expect(replies.mock.calls[0][0]).toMatchObject({ error: { data: { reason: 'permission_denied' } } });
    expect(mocks.showAppNotification).not.toHaveBeenCalled();
    expect(mocks.reportAppActivity).toHaveBeenCalledWith('art-1', 'a'.repeat(64), {
      capability: 'notify',
      outcome: 'denied',
      detail: null,
    });
  });

  it('applies a decision saved after the page loaded without remounting', async () => {
    const view = setup({ notify: 'ask', fetch: 'ask' });
    view.send(notify);
    await waitFor(() => expect(view.replies).toHaveBeenCalledTimes(1));

    view.rerender(<Harness statuses={ALLOWED} />);
    view.send({ ...notify, id: 2 });

    await waitFor(() => expect(view.replies).toHaveBeenCalledTimes(2));
    expect(view.replies.mock.calls[1][0]).toEqual({ jsonrpc: '2.0', id: 2, result: null });
  });

  it('stops listening once the host page unmounts', async () => {
    const { send, replies, unmount } = setup();
    unmount();

    send(notify);
    await new Promise((resolve) => setTimeout(resolve, 20));

    expect(replies).not.toHaveBeenCalled();
  });
});
