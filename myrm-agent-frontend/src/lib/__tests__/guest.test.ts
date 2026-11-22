/**
 * A tab hears about a sign-in, sign-out or account switch made in another tab through the browser's `storage` event. The
 * tab that made the change gets no event, so the subscription never fires for a tab's own writes.
 */

import { afterEach, describe, expect, it, vi } from 'vitest';
import { onAuthTokenChangedElsewhere } from '@/lib/guest';

const tokenEvent = (init: StorageEventInit) => new StorageEvent('storage', { key: 'auth_token', ...init });

describe('onAuthTokenChangedElsewhere', () => {
  const subscriptions: Array<() => void> = [];
  const subscribe = (onChange: () => void) => {
    const unsubscribe = onAuthTokenChangedElsewhere(onChange);
    subscriptions.push(unsubscribe);
    return unsubscribe;
  };

  afterEach(() => {
    subscriptions.splice(0).forEach((unsubscribe) => unsubscribe());
  });

  it('calls back when another tab signs in or switches account', () => {
    const onChange = vi.fn();
    subscribe(onChange);

    window.dispatchEvent(tokenEvent({ oldValue: null, newValue: 'token-a' }));
    window.dispatchEvent(tokenEvent({ oldValue: 'token-a', newValue: 'token-b' }));

    expect(onChange).toHaveBeenCalledTimes(2);
  });

  it('calls back when another tab signs out', () => {
    const onChange = vi.fn();
    subscribe(onChange);

    window.dispatchEvent(tokenEvent({ oldValue: 'token-a', newValue: null }));

    expect(onChange).toHaveBeenCalledTimes(1);
  });

  it('ignores a change of any other key', () => {
    const onChange = vi.fn();
    subscribe(onChange);

    window.dispatchEvent(new StorageEvent('storage', { key: 'theme', oldValue: 'light', newValue: 'dark' }));

    expect(onChange).not.toHaveBeenCalled();
  });

  it('stops calling back once unsubscribed', () => {
    const onChange = vi.fn();
    const unsubscribe = subscribe(onChange);

    unsubscribe();
    window.dispatchEvent(tokenEvent({ oldValue: 'token-a', newValue: 'token-b' }));

    expect(onChange).not.toHaveBeenCalled();
  });
});
