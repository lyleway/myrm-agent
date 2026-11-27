/**
 * [INPUT]
 * - ../appSurface::appSurface / releaseAppSurface / onAppSurfaceEvent
 *
 * [OUTPUT]
 * - Tests: each surface call reaches its shell command; a taken key combination is told apart from a failure; events
 *   are received through the window's own listeners; nothing throws.
 *
 * [POS]
 * Desktop-only transport of the app window's tray item, key combinations and floating.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  invoke: vi.fn(),
  listen: vi.fn(),
  getCurrentWebviewWindow: vi.fn(),
}));
vi.mock('@tauri-apps/api/core', () => ({ invoke: mocks.invoke }));
vi.mock('@tauri-apps/api/webviewWindow', () => ({ getCurrentWebviewWindow: mocks.getCurrentWebviewWindow }));

import { appSurface, onAppSurfaceEvent, releaseAppSurface } from '../appSurface';

describe('appSurface', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
    mocks.invoke.mockResolvedValue(undefined);
  });

  it('sends each call to its command', async () => {
    await expect(appSurface.setTray('3', 'Inbox')).resolves.toBe(true);
    await expect(appSurface.clearTray()).resolves.toBe(true);
    await expect(appSurface.unregisterShortcut('Alt+F1')).resolves.toBe(true);
    await expect(appSurface.setFloat(true)).resolves.toBe(true);

    expect(mocks.invoke.mock.calls).toEqual([
      ['set_app_tray', { title: '3', tooltip: 'Inbox' }],
      ['clear_app_tray', {}],
      ['unregister_app_shortcut', { accelerator: 'Alt+F1' }],
      ['set_app_window_always_on_top', { alwaysOnTop: true }],
    ]);
  });

  it('reports a refused call as false instead of throwing', async () => {
    mocks.invoke.mockRejectedValue('denied');

    await expect(appSurface.setTray('3', '')).resolves.toBe(false);
    await expect(appSurface.clearTray()).resolves.toBe(false);
    await expect(appSurface.setFloat(false)).resolves.toBe(false);
  });

  describe('registerShortcut', () => {
    it('registers the combination', async () => {
      await expect(appSurface.registerShortcut('Alt+F1')).resolves.toBe('ok');
      expect(mocks.invoke).toHaveBeenCalledWith('register_app_shortcut', { accelerator: 'Alt+F1' });
    });

    it('tells a combination someone else holds apart from a failure', async () => {
      mocks.invoke.mockRejectedValueOnce('conflict');
      await expect(appSurface.registerShortcut('Alt+F1')).resolves.toBe('conflict');

      mocks.invoke.mockRejectedValueOnce(new Error('boom'));
      await expect(appSurface.registerShortcut('Alt+F1')).resolves.toBe('failed');
    });
  });

  it('gives back the tray item and every key combination in one go', async () => {
    await releaseAppSurface();

    expect(mocks.invoke.mock.calls).toEqual(
      expect.arrayContaining([
        ['clear_app_tray', {}],
        ['unregister_app_shortcut', { accelerator: null }],
      ]),
    );
  });
});

describe('onAppSurfaceEvent', () => {
  const handlers = { onShortcut: vi.fn(), onOpenPermissions: vi.fn() };

  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  it('listens on this window, not on the untargeted channel', async () => {
    const stops = [vi.fn(), vi.fn()];
    mocks.listen.mockResolvedValueOnce(stops[0]).mockResolvedValueOnce(stops[1]);
    mocks.getCurrentWebviewWindow.mockReturnValue({ listen: mocks.listen });

    const stop = await onAppSurfaceEvent(handlers);

    const byName = Object.fromEntries(mocks.listen.mock.calls.map(([name, callback]) => [name, callback]));
    byName['app-surface:shortcut']({ payload: { accelerator: 'Alt+F1' } });
    byName['app-surface:permissions']({ payload: null });
    expect(handlers.onShortcut).toHaveBeenCalledWith('Alt+F1');
    expect(handlers.onOpenPermissions).toHaveBeenCalledTimes(1);

    stop();
    expect(stops[0]).toHaveBeenCalledTimes(1);
    expect(stops[1]).toHaveBeenCalledTimes(1);
  });

  it('returns a stop that does nothing when the window cannot listen', async () => {
    mocks.getCurrentWebviewWindow.mockImplementation(() => {
      throw new Error('no window');
    });

    const stop = await onAppSurfaceEvent(handlers);

    expect(() => stop()).not.toThrow();
  });
});
