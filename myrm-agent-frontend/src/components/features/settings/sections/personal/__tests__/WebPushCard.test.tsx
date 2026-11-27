/**
 * [INPUT]
 * - @/components/features/settings/sections/personal/WebPushCard
 * - @/hooks/pwa/usePushSubscription: replaced by a fake that holds a subscription
 *
 * [OUTPUT]
 * - Test suite for the test button on the personal Web Push card
 *
 * [POS]
 * Pins what the user is told after pressing the test button: a test that reached no device is announced as a
 * failure and its reason stays on the card, never as a success; a test that reached devices names how many.
 */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { usePushSubscription } from '@/hooks/pwa/usePushSubscription';
import { toast } from '@/lib/utils/toast';
import WebPushCard from '../WebPushCard';

type PushSubscriptionHook = ReturnType<typeof usePushSubscription>;

const subscription = vi.hoisted(() => ({ current: null as PushSubscriptionHook | null }));

vi.mock('next-intl', () => {
  const t = (key: string, values?: Record<string, string>) => (values ? `${key}:${values.count}` : key);
  return { useTranslations: () => t };
});

vi.mock('@/hooks/pwa/usePushSubscription', () => ({ usePushSubscription: () => subscription.current }));

vi.mock('@/hooks/pwa/usePWAInstall', () => ({
  usePWAInstall: () => ({ isInstalled: true, isInstallable: false, promptInstall: vi.fn() }),
}));

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
    fireEvent.click(screen.getByRole('button', { name: 'webPushSendTest' }));
  });
}

describe('WebPushCard test button', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('announces a test that reached no device as a failure and keeps the reason on the card', async () => {
    const reason = 'HTTP 403 Forbidden';
    subscribedBrowser(vi.fn().mockRejectedValue(new Error(reason)), reason);
    render(<WebPushCard />);

    await pressTest();

    expect(toast.error).toHaveBeenCalledWith('webPushTestFailed');
    expect(toast.success).not.toHaveBeenCalled();
    expect(screen.getByText(reason)).toBeTruthy();
  });

  it('names how many devices the test reached', async () => {
    subscribedBrowser(vi.fn().mockResolvedValue(2));
    render(<WebPushCard />);

    await pressTest();

    expect(toast.success).toHaveBeenCalledWith('webPushTestSent:2');
    expect(toast.error).not.toHaveBeenCalled();
  });
});
