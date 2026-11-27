/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import DeferredAppInitializers from '../deferred-app-initializers';

const isAppWindow = vi.hoisted(() => vi.fn());
vi.mock('@/hooks/tauri/useIsAppWindow', () => ({ useIsAppWindow: isAppWindow }));

// next/dynamic resolves its loader on the client after mount; a lazy component makes the same load observable here.
vi.mock('next/dynamic', async () => {
  const React = await import('react');
  return {
    default: (loader: () => Promise<{ default: React.ComponentType }>) => {
      const Lazy = React.lazy(loader);
      return function DynamicStandIn() {
        return (
          <React.Suspense fallback={null}>
            <Lazy />
          </React.Suspense>
        );
      };
    },
  };
});

vi.mock('../deferred-mount', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="deferred-mount">{children}</div>,
}));

vi.mock('../flow-pad-modal', () => ({ FlowPadModal: () => <div data-testid="flow-pad-modal" /> }));
vi.mock('../service-worker-registrar', () => ({
  ServiceWorkerRegistrar: () => <div data-testid="service-worker-registrar" />,
}));
vi.mock('../app-update-prompt', () => ({ AppUpdatePrompt: () => <div data-testid="app-update-prompt" /> }));
vi.mock('../whats-new-modal', () => ({ WhatsNewModal: () => <div data-testid="whats-new-modal" /> }));
vi.mock('../update-handoff-notifier', () => ({
  UpdateHandoffNotifier: () => <div data-testid="update-handoff-notifier" />,
}));
vi.mock('@/components/features/instance-archive/OutcomeNotifier', () => ({
  OutcomeNotifier: () => <div data-testid="outcome-notifier" />,
}));
vi.mock('../appshot-initializer', () => ({ default: () => <div data-testid="appshot-initializer" /> }));
vi.mock('../voice-ptt-initializer', () => ({ default: () => <div data-testid="voice-ptt-initializer" /> }));

const MAIN_WINDOW_INITIALIZERS = [
  'flow-pad-modal',
  'service-worker-registrar',
  'app-update-prompt',
  'whats-new-modal',
  'update-handoff-notifier',
  'outcome-notifier',
  'appshot-initializer',
  'voice-ptt-initializer',
];

describe('DeferredAppInitializers Component', () => {
  beforeEach(() => isAppWindow.mockReturnValue(false));

  it('renders deferred initializers including UpdateHandoffNotifier', () => {
    const { getByTestId } = render(<DeferredAppInitializers />);
    expect(getByTestId('deferred-mount')).toBeInTheDocument();
  });

  it('loads every app-wide initializer in the main window', async () => {
    render(<DeferredAppInitializers />);

    for (const id of MAIN_WINDOW_INITIALIZERS) {
      expect(await screen.findByTestId(id)).toBeInTheDocument();
    }
  });

  it('mounts none of them in an installed app window', () => {
    isAppWindow.mockReturnValue(true);

    const { container } = render(<DeferredAppInitializers />);

    expect(container).toBeEmptyDOMElement();
  });
});
