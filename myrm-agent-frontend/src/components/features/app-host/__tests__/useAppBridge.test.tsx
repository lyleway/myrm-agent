/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../useAppBridge::useAppBridge
 *
 * [OUTPUT]
 * - Tests: only the window of the embedded iframe is answered; its requests reach the notification, the fetch proxy and
 *   the desktop surface; the shell's events reach the page; a revoked permission takes its desktop side down.
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
  proxyAppAi: vi.fn(),
  reportAppActivity: vi.fn(),
  stopEvents: vi.fn(),
  surfaceEvents: {
    current: null as null | { onShortcut: (accelerator: string) => void; onOpenPermissions: () => void },
  },
}));

vi.mock('@/services/appNotification', () => ({ showAppNotification: mocks.showAppNotification }));
vi.mock('@/services/artifactApps', () => ({
  proxyAppFetch: mocks.proxyAppFetch,
  proxyAppAi: mocks.proxyAppAi,
  reportAppActivity: mocks.reportAppActivity,
}));

vi.mock('@/lib/desktop/appSurface', () => ({
  onAppSurfaceEvent: vi.fn(async (handlers: NonNullable<typeof mocks.surfaceEvents.current>) => {
    mocks.surfaceEvents.current = handlers;
    return mocks.stopEvents;
  }),
}));

import type { AppSurfaceDeps } from '@/lib/app-bridge/hostTypes';
import { useAppBridge } from '../useAppBridge';

const ALLOWED: Record<BridgeCapability, CapabilityStatus> = {
  notify: 'allowed',
  fetch: 'allowed',
  ai: 'allowed',
  tray: 'allowed',
  shortcut: 'allowed',
  float: 'allowed',
};

const surface: AppSurfaceDeps = {
  setTray: vi.fn(),
  clearTray: vi.fn(),
  registerShortcut: vi.fn(),
  unregisterShortcut: vi.fn(),
  setFloat: vi.fn(),
};
const onFloatChange = vi.fn();
const onOpenPermissions = vi.fn();

interface HarnessProps {
  statuses?: Record<BridgeCapability, CapabilityStatus>;
  desktop?: boolean;
}

function Harness({ statuses = ALLOWED, desktop = false }: HarnessProps) {
  const ref = useRef<HTMLDivElement>(null);
  useAppBridge({
    artifactId: 'art-1',
    appName: 'Weather',
    versionSha256: 'a'.repeat(64),
    statuses,
    declaredShortcuts: ['Alt+F1'],
    frameHostRef: ref,
    surface: desktop ? surface : null,
    onFloatChange,
    onOpenPermissions,
  });
  return (
    <div ref={ref}>
      <iframe title="app" />
    </div>
  );
}

function setup(statuses?: Record<BridgeCapability, CapabilityStatus>, desktop = false) {
  const view = render(<Harness statuses={statuses} desktop={desktop} />);
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
    mocks.surfaceEvents.current = null;
    vi.mocked(surface.setTray).mockResolvedValue(true);
    vi.mocked(surface.clearTray).mockResolvedValue(true);
    vi.mocked(surface.registerShortcut).mockResolvedValue('ok');
    vi.mocked(surface.unregisterShortcut).mockResolvedValue(true);
    vi.mocked(surface.setFloat).mockResolvedValue(true);
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

  it('sends a granted model call through the proxy and hands the page only the answer text', async () => {
    mocks.proxyAppAi.mockResolvedValue({ ok: true, text: 'Sunny', code: null, message: null });
    const { send, replies } = setup();

    send({ jsonrpc: '2.0', id: 'a1', method: 'ai.complete', params: { prompt: 'Weather?', maxTokens: 64 } });

    await waitFor(() =>
      expect(replies).toHaveBeenCalledWith({ jsonrpc: '2.0', id: 'a1', result: { text: 'Sunny' } }, '*'),
    );
    expect(mocks.proxyAppAi).toHaveBeenCalledWith('art-1', {
      prompt: 'Weather?',
      system: null,
      maxTokens: 64,
      version_sha256: 'a'.repeat(64),
    });
  });

  it('refuses while the capability is undecided and logs the refusal for the user', async () => {
    const { send, replies } = setup({ ...ALLOWED, notify: 'ask', fetch: 'ask' });

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
    const view = setup({ ...ALLOWED, notify: 'ask', fetch: 'ask' });
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

  describe('the desktop surface', () => {
    const trayRequest = { jsonrpc: '2.0', id: 7, method: 'tray.set', params: { title: '3 unread' } };
    const eventTo = (name: string, data: unknown) => ({ jsonrpc: '2.0', method: 'myrm/event', params: { name, data } });

    it('reaches the shell only in the app’s own desktop window', async () => {
      const web = setup();
      web.send(trayRequest);
      await waitFor(() => expect(web.replies).toHaveBeenCalled());
      expect(web.replies.mock.calls[0][0]).toMatchObject({ error: { data: { reason: 'unsupported' } } });
      expect(surface.setTray).not.toHaveBeenCalled();
      web.unmount();

      const desktop = setup(undefined, true);
      desktop.send(trayRequest);
      await waitFor(() => expect(desktop.replies).toHaveBeenCalledWith({ jsonrpc: '2.0', id: 7, result: null }, '*'));
      expect(surface.setTray).toHaveBeenCalledWith('3 unread', '');
    });

    it('keeps the host’s keep-on-top menu in step with what the page asks for', async () => {
      const { send, replies } = setup(undefined, true);

      send({ jsonrpc: '2.0', id: 1, method: 'float.set', params: { on: true } });

      await waitFor(() => expect(replies).toHaveBeenCalled());
      expect(onFloatChange).toHaveBeenCalledWith(true);
    });

    it('tells the page when the user presses a key combination it registered', async () => {
      const { replies } = setup(undefined, true);
      await waitFor(() => expect(mocks.surfaceEvents.current).not.toBeNull());

      mocks.surfaceEvents.current?.onShortcut('Alt+F1');

      expect(replies).toHaveBeenCalledWith(eventTo('shortcut', { accelerator: 'Alt+F1' }), '*');
    });

    it('does not tell the page about a key combination once the permission is gone', async () => {
      const { replies } = setup({ ...ALLOWED, shortcut: 'denied' }, true);
      await waitFor(() => expect(mocks.surfaceEvents.current).not.toBeNull());

      mocks.surfaceEvents.current?.onShortcut('Alt+F1');

      expect(replies).not.toHaveBeenCalled();
    });

    it('opens the permissions from the tray menu', async () => {
      setup(undefined, true);
      await waitFor(() => expect(mocks.surfaceEvents.current).not.toBeNull());

      mocks.surfaceEvents.current?.onOpenPermissions();

      expect(onOpenPermissions).toHaveBeenCalledTimes(1);
    });

    it('takes the tray item down and tells the page when the permission is revoked', async () => {
      const view = setup(undefined, true);
      view.send(trayRequest);
      await waitFor(() => expect(view.replies).toHaveBeenCalledTimes(1));

      view.rerender(<Harness statuses={{ ...ALLOWED, tray: 'denied' }} desktop />);

      await waitFor(() => expect(surface.clearTray).toHaveBeenCalledTimes(1));
      expect(view.replies).toHaveBeenCalledWith(eventTo('permission-revoked', { capability: 'tray' }), '*');
    });

    it('gives back what the page set up, and stops listening, when the host page goes away', async () => {
      const view = setup(undefined, true);
      view.send(trayRequest);
      await waitFor(() => expect(view.replies).toHaveBeenCalledTimes(1));
      await waitFor(() => expect(mocks.surfaceEvents.current).not.toBeNull());

      view.unmount();

      await waitFor(() => expect(surface.clearTray).toHaveBeenCalledTimes(1));
      expect(mocks.stopEvents).toHaveBeenCalledTimes(1);
    });
  });
});
