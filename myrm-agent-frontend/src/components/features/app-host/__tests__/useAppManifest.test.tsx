/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../useAppManifest::useAppManifest
 *
 * [OUTPUT]
 * - Tests: the page's manifest link names this app while the host page is shown and goes back afterwards; the desktop
 *   shell and a page without a name leave it alone.
 *
 * [POS]
 * What decides whether the browser's "Install" makes a window for the app or for the whole site.
 */

import { renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({ isTauriRuntime: vi.fn() }));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: mocks.isTauriRuntime }));

import { useAppManifest } from '../useAppManifest';

const href = () => document.querySelector('link[rel="manifest"]')?.getAttribute('href');

describe('useAppManifest', () => {
  beforeEach(() => {
    mocks.isTauriRuntime.mockReturnValue(false);
    document.head.insertAdjacentHTML('beforeend', '<link rel="manifest" href="/manifest.json">');
  });

  afterEach(() => {
    document.head.querySelector('link[rel="manifest"]')?.remove();
  });

  it('points the browser at this app while the page is shown, and back at the site afterwards', () => {
    const { unmount } = renderHook(() => useAppManifest('art-1', 'Weather'));

    expect(href()).toBe('/apps/art-1/manifest.webmanifest?name=Weather');

    unmount();
    expect(href()).toBe('/manifest.json');
  });

  it('follows the app’s name', () => {
    const { rerender } = renderHook(({ name }) => useAppManifest('art-1', name), { initialProps: { name: 'Weather' } });

    rerender({ name: 'Rain' });

    expect(href()).toBe('/apps/art-1/manifest.webmanifest?name=Rain');
  });

  it('waits for the name before touching the link', () => {
    renderHook(() => useAppManifest('art-1', undefined));

    expect(href()).toBe('/manifest.json');
  });

  it('leaves the link alone in the desktop shell', () => {
    mocks.isTauriRuntime.mockReturnValue(true);

    renderHook(() => useAppManifest('art-1', 'Weather'));

    expect(href()).toBe('/manifest.json');
  });

  it('does nothing on a page without a manifest link', () => {
    document.head.querySelector('link[rel="manifest"]')?.remove();

    expect(() => renderHook(() => useAppManifest('art-1', 'Weather'))).not.toThrow();
  });
});
