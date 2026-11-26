import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ isTauriRuntime: vi.fn(), openAppWindow: vi.fn() }));

vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: mocks.isTauriRuntime }));
vi.mock('@/lib/desktop/appWindow', () => ({ openAppWindow: mocks.openAppWindow }));

import { openApp } from '../openApp';

describe('openApp', () => {
  const navigate = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('navigates to the app page in a browser', async () => {
    mocks.isTauriRuntime.mockReturnValue(false);

    await openApp('art-1', 'Weather', navigate);

    expect(navigate).toHaveBeenCalledWith('/apps/art-1');
    expect(mocks.openAppWindow).not.toHaveBeenCalled();
  });

  it('opens the app window on desktop and stays on the current page', async () => {
    mocks.isTauriRuntime.mockReturnValue(true);
    mocks.openAppWindow.mockResolvedValue(true);

    await openApp('art-1', 'Weather', navigate);

    expect(mocks.openAppWindow).toHaveBeenCalledWith('art-1', 'Weather');
    expect(navigate).not.toHaveBeenCalled();
  });

  it('falls back to the app page when the desktop window cannot be opened', async () => {
    mocks.isTauriRuntime.mockReturnValue(true);
    mocks.openAppWindow.mockResolvedValue(false);

    await openApp('art-1', 'Weather', navigate);

    expect(navigate).toHaveBeenCalledWith('/apps/art-1');
  });
});
