/**
 * @vitest-environment jsdom
 */
import { beforeEach, describe, it, expect, vi } from 'vitest';
import { render } from '@testing-library/react';
import DeferredAppInitializers from '../deferred-app-initializers';

const isAppWindow = vi.hoisted(() => vi.fn());
vi.mock('@/hooks/tauri/useIsAppWindow', () => ({ useIsAppWindow: isAppWindow }));

vi.mock('../deferred-mount', () => ({
  default: ({ children }: { children: React.ReactNode }) => <div data-testid="deferred-mount">{children}</div>,
}));

vi.mock('../update-handoff-notifier', () => ({
  UpdateHandoffNotifier: () => <div data-testid="update-handoff-notifier" />,
}));

describe('DeferredAppInitializers Component', () => {
  beforeEach(() => isAppWindow.mockReturnValue(false));

  it('renders deferred initializers including UpdateHandoffNotifier', () => {
    const { getByTestId } = render(<DeferredAppInitializers />);
    expect(getByTestId('deferred-mount')).toBeInTheDocument();
  });

  it('mounts none of them in an installed app window', () => {
    isAppWindow.mockReturnValue(true);

    const { container } = render(<DeferredAppInitializers />);

    expect(container).toBeEmptyDOMElement();
  });
});
