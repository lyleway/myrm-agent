import { beforeEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.hoisted(() => vi.fn());
vi.mock('@tauri-apps/api/core', () => ({ invoke }));

import { closeAppWindow, openAppWindow, setAppWindowAlwaysOnTop } from '../appWindow';

describe('app window control', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  it('invokes each desktop command with its arguments and reports success', async () => {
    invoke.mockResolvedValue(undefined);

    await expect(openAppWindow('art-1', 'Weather')).resolves.toBe(true);
    await expect(closeAppWindow()).resolves.toBe(true);
    await expect(setAppWindowAlwaysOnTop(true)).resolves.toBe(true);

    expect(invoke.mock.calls).toEqual([
      ['open_app_window', { artifactId: 'art-1', title: 'Weather' }],
      ['close_app_window', {}],
      ['set_app_window_always_on_top', { alwaysOnTop: true }],
    ]);
  });

  it('reports failure instead of throwing when the command is rejected', async () => {
    invoke.mockRejectedValue(new Error('no such window'));

    await expect(openAppWindow('art-1', 'Weather')).resolves.toBe(false);
    await expect(closeAppWindow()).resolves.toBe(false);
    await expect(setAppWindowAlwaysOnTop(false)).resolves.toBe(false);
  });
});
