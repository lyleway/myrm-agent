/** @vitest-environment jsdom */
/**
 * What a page holds in memory (the config cache, the stores, edits not yet sent) belongs to the account that was signed
 * in when the page loaded, and the page keeps reading the signed-in token per request. When another tab signs in as
 * someone else or signs out, this page must start over instead of carrying on as the account it loaded for.
 */

import { render } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import AuthInitializer from '../auth-initializer';

vi.mock('next/navigation', () => ({
  usePathname: () => '/chat',
}));
vi.mock('@/store/useAuthStore', () => ({
  default: () => ({ isInitialized: true, initAuth: vi.fn(), initTauriLocalUser: vi.fn() }),
}));
vi.mock('../auth-callback', () => ({
  default: () => null,
}));
vi.mock('@/lib/deploy-mode', () => ({
  isTauriRuntime: () => false,
  isLocalMode: () => false,
  shouldRedirectToLoginOnAuthFailure: () => false,
  isRemoteGatewayActive: () => false,
}));

const announceFromAnotherTab = (key: string, oldValue: string | null, newValue: string | null) =>
  window.dispatchEvent(new StorageEvent('storage', { key, oldValue, newValue }));

describe('AuthInitializer when another tab changes the signed-in account', () => {
  const originalLocation = window.location;
  const reload = vi.fn();

  beforeEach(() => {
    Object.defineProperty(window, 'location', {
      value: { ...originalLocation, reload },
      writable: true,
    });
  });

  afterEach(() => {
    reload.mockClear();
    Object.defineProperty(window, 'location', { value: originalLocation, writable: true });
  });

  it('reloads the page when another tab signs in as a different account', () => {
    render(<AuthInitializer />);

    announceFromAnotherTab('auth_token', 'token-a', 'token-b');

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('reloads the page when another tab signs out', () => {
    render(<AuthInitializer />);

    announceFromAnotherTab('auth_token', 'token-a', null);

    expect(reload).toHaveBeenCalledTimes(1);
  });

  it('leaves the page alone when another tab changes something else', () => {
    render(<AuthInitializer />);

    announceFromAnotherTab('theme', 'light', 'dark');

    expect(reload).not.toHaveBeenCalled();
  });

  it('stops watching once the initializer is unmounted', () => {
    const { unmount } = render(<AuthInitializer />);

    unmount();
    announceFromAnotherTab('auth_token', 'token-a', 'token-b');

    expect(reload).not.toHaveBeenCalled();
  });
});
