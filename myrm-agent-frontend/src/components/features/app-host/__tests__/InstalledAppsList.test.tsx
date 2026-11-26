/** @vitest-environment jsdom */

import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  openApp: vi.fn(),
  refresh: vi.fn().mockResolvedValue(undefined),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('../openApp', () => ({ openApp: mocks.openApp }));
vi.mock('@/services/artifactApps', () => ({ listInstalledApps: vi.fn() }));

import { useArtifactAppsStore } from '@/store/useArtifactAppsStore';
import { InstalledAppsList } from '../InstalledAppsList';

const app = (id: string, name: string) => ({
  artifact_id: id,
  name,
  installed_at: '2026-11-26T00:00:00Z',
  updated_at: '2026-11-26T00:00:00Z',
});

describe('InstalledAppsList', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useArtifactAppsStore.setState({ apps: [], loaded: true, refresh: mocks.refresh });
  });

  it('renders nothing while there is no installed app', () => {
    const { container } = render(<InstalledAppsList />);

    expect(container).toBeEmptyDOMElement();
  });

  it('reads the registry when it appears', () => {
    render(<InstalledAppsList />);

    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it('reads the registry again when the window regains focus, and stops listening on unmount', () => {
    const { unmount } = render(<InstalledAppsList />);

    window.dispatchEvent(new Event('focus'));
    expect(mocks.refresh).toHaveBeenCalledTimes(2);

    unmount();
    window.dispatchEvent(new Event('focus'));
    expect(mocks.refresh).toHaveBeenCalledTimes(2);
  });

  it('lists each installed app and opens the one that is clicked', () => {
    useArtifactAppsStore.setState({ apps: [app('a', 'weather.html'), app('b', 'timer.html')] });
    render(<InstalledAppsList />);

    expect(screen.getAllByRole('listitem')).toHaveLength(2);
    fireEvent.click(screen.getByRole('button', { name: /timer\.html/ }));

    expect(mocks.openApp).toHaveBeenCalledWith('b', 'timer.html', mocks.push);
  });
});
