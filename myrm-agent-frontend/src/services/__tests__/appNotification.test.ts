import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  isTauriRuntime: vi.fn(),
  notify: vi.fn(),
  requestPermission: vi.fn(),
  sendTauriNativeNotification: vi.fn(),
  isPermissionGranted: vi.fn(),
  requestTauriPermission: vi.fn(),
}));

vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: mocks.isTauriRuntime }));
vi.mock('@/services/notification', () => ({
  notificationService: { notify: mocks.notify, requestPermission: mocks.requestPermission },
}));
vi.mock('@/services/tauriNativeNotification', () => ({
  sendTauriNativeNotification: mocks.sendTauriNativeNotification,
}));
vi.mock('@tauri-apps/plugin-notification', () => ({
  isPermissionGranted: mocks.isPermissionGranted,
  requestPermission: mocks.requestTauriPermission,
}));

import { requestAppNotificationPermission, showAppNotification } from '../appNotification';

function focus(hasFocus: boolean) {
  vi.spyOn(document, 'hasFocus').mockReturnValue(hasFocus);
}

describe('showAppNotification', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.restoreAllMocks();
    mocks.isTauriRuntime.mockReturnValue(false);
  });

  it('uses the browser notification service outside the desktop shell', async () => {
    focus(false);

    await showAppNotification('Weather · Rain', 'Soon');

    expect(mocks.notify).toHaveBeenCalledWith('Weather · Rain', { body: 'Soon' });
    expect(mocks.sendTauriNativeNotification).not.toHaveBeenCalled();
  });

  it('sends a native notification from an unfocused desktop window', async () => {
    mocks.isTauriRuntime.mockReturnValue(true);
    mocks.sendTauriNativeNotification.mockResolvedValue(true);
    focus(false);

    await showAppNotification('Weather · Rain', 'Soon');

    expect(mocks.sendTauriNativeNotification).toHaveBeenCalledWith({ title: 'Weather · Rain', body: 'Soon' });
    expect(mocks.notify).not.toHaveBeenCalled();
  });

  it('shows a toast in a focused desktop window instead of an OS notification', async () => {
    mocks.isTauriRuntime.mockReturnValue(true);
    focus(true);

    await showAppNotification('Weather · Rain', 'Soon');

    expect(mocks.sendTauriNativeNotification).not.toHaveBeenCalled();
    expect(mocks.notify).toHaveBeenCalledWith('Weather · Rain', { body: 'Soon' });
  });

  it('falls back to the in-app path when the OS refuses the native notification', async () => {
    mocks.isTauriRuntime.mockReturnValue(true);
    mocks.sendTauriNativeNotification.mockResolvedValue(false);
    focus(false);

    await showAppNotification('Weather · Rain', 'Soon');

    expect(mocks.notify).toHaveBeenCalledWith('Weather · Rain', { body: 'Soon' });
  });
});

describe('requestAppNotificationPermission', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.isTauriRuntime.mockReturnValue(false);
  });

  it('asks the browser outside the desktop shell', async () => {
    await requestAppNotificationPermission();

    expect(mocks.requestPermission).toHaveBeenCalledTimes(1);
  });

  it('asks the desktop plugin only when it is not granted yet', async () => {
    mocks.isTauriRuntime.mockReturnValue(true);
    mocks.isPermissionGranted.mockResolvedValueOnce(false).mockResolvedValueOnce(true);

    await requestAppNotificationPermission();
    await requestAppNotificationPermission();

    expect(mocks.requestTauriPermission).toHaveBeenCalledTimes(1);
    expect(mocks.requestPermission).not.toHaveBeenCalled();
  });

  it('never throws when the plugin is unavailable', async () => {
    mocks.isTauriRuntime.mockReturnValue(true);
    mocks.isPermissionGranted.mockRejectedValue(new Error('no plugin'));

    await expect(requestAppNotificationPermission()).resolves.toBeUndefined();
  });
});
