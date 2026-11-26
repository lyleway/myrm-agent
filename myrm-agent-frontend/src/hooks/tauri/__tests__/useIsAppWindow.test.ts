/** @vitest-environment jsdom */

import { renderHook } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const isTauriRuntime = vi.hoisted(() => vi.fn());
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime }));

import { useIsAppWindow } from '../useIsAppWindow';

function visit(url: string, tauri: boolean) {
  isTauriRuntime.mockReturnValue(tauri);
  window.history.replaceState({}, '', url);
}

describe('useIsAppWindow', () => {
  beforeEach(() => vi.clearAllMocks());

  it('is true for the focused app route inside the desktop shell', () => {
    visit('/apps/art-1?mode=focused', true);

    expect(renderHook(() => useIsAppWindow()).result.current).toBe(true);
  });

  it.each([
    ['a browser tab on the app route', '/apps/art-1?mode=focused', false],
    ['the app route without the focused flag', '/apps/art-1', true],
    ['a focused session window', '/chat-1?mode=focused', true],
    ['the artifacts page', '/artifacts', true],
    ['a route that only resembles it', '/appsettings/x?mode=focused', true],
  ])('is false for %s', (_name, url, tauri) => {
    visit(url, tauri);

    expect(renderHook(() => useIsAppWindow()).result.current).toBe(false);
  });
});
