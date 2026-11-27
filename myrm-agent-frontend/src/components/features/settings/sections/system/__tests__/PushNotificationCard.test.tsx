/**
 * [INPUT]
 * - @/components/features/settings/sections/system/PushNotificationCard
 * - @/hooks/pwa/usePushSubscription: replaced by a fake that holds a subscription
 *
 * [OUTPUT]
 * - Test suite for the test button on the system Web Push card
 *
 * [POS]
 * Pins what the user is told after pressing the test button: a test that reached no device keeps its reason on
 * the card and is never announced as sent; a test that reached devices is announced.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { usePushSubscription } from '@/hooks/pwa/usePushSubscription';
import { toast } from '@/lib/utils/toast';
import PushNotificationCard from '../PushNotificationCard';

type PushSubscriptionHook = ReturnType<typeof usePushSubscription>;

const subscription = vi.hoisted(() => ({ current: null as PushSubscriptionHook | null }));

vi.mock('next-intl', () => {
  const t = (key: string) => key;
  return { useTranslations: () => t };
});

vi.mock('@/hooks/pwa/usePushSubscription', () => ({ usePushSubscription: () => subscription.current }));

vi.mock('@/lib/utils/toast', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

/** A browser that holds a subscription; `error` is what the hook keeps after a failed test. */
function subscribedBrowser(sendTest: PushSubscriptionHook['sendTest'], error: string | null = null) {
  subscription.current = {
    state: 'subscribed',
    loading: false,
    error,
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    sendTest,
  };
}

async function pressTest() {
  await act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'push.test' }));
  });
}

describe('PushNotificationCard test button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('keeps the reason of a test that reached no device on the card and does not announce it as sent', async () => {
    const reason = 'HTTP 403 Forbidden';
    const sendTest = vi.fn().mockRejectedValue(new Error(reason));
    subscribedBrowser(sendTest, reason);
    render(<PushNotificationCard />);

    await pressTest();

    expect(sendTest).toHaveBeenCalledTimes(1);
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByText(reason)).toBeTruthy();
  });

  it('announces a test that reached devices as sent', async () => {
    subscribedBrowser(vi.fn().mockResolvedValue(1));
    render(<PushNotificationCard />);

    await pressTest();

    expect(toast.success).toHaveBeenCalledWith('pushTestSent');
  });
});
