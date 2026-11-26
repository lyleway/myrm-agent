/** @vitest-environment jsdom */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  openApp: vi.fn(),
  install: vi.fn(),
  refresh: vi.fn().mockResolvedValue(undefined),
  toastError: vi.fn(),
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }));
vi.mock('@/components/features/app-host/openApp', () => ({ openApp: mocks.openApp }));
vi.mock('@/services/artifactApps', () => ({ listInstalledApps: vi.fn() }));

import { useArtifactAppsStore } from '@/store/useArtifactAppsStore';
import { ArtifactAppButton } from '../ArtifactAppButton';

function seed(apps: string[], loaded = true) {
  useArtifactAppsStore.setState({
    apps: apps.map((id) => ({ artifact_id: id, name: `${id}.html`, installed_at: '', updated_at: '' })),
    loaded,
    install: mocks.install,
    refresh: mocks.refresh,
  });
}

describe('ArtifactAppButton', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.install.mockResolvedValue(undefined);
    mocks.openApp.mockResolvedValue(undefined);
  });

  it('installs, then opens, when the artifact is not an app yet', async () => {
    seed([]);
    render(<ArtifactAppButton artifactId="art-1" name="weather.html" />);

    fireEvent.click(screen.getByRole('button', { name: 'install' }));

    await waitFor(() => expect(mocks.openApp).toHaveBeenCalledWith('art-1', 'weather.html', mocks.push));
    expect(mocks.install).toHaveBeenCalledWith('art-1');
    expect(mocks.install.mock.invocationCallOrder[0]).toBeLessThan(mocks.openApp.mock.invocationCallOrder[0]);
  });

  it('only opens an artifact that is already an app', async () => {
    seed(['art-1']);
    render(<ArtifactAppButton artifactId="art-1" name="weather.html" />);

    fireEvent.click(screen.getByRole('button', { name: 'open' }));

    await waitFor(() => expect(mocks.openApp).toHaveBeenCalled());
    expect(mocks.install).not.toHaveBeenCalled();
  });

  it('tells the user when installing fails and does not open anything', async () => {
    seed([]);
    mocks.install.mockRejectedValue(new Error('only html'));
    render(<ArtifactAppButton artifactId="art-1" name="weather.html" />);

    fireEvent.click(screen.getByRole('button', { name: 'install' }));

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('installFailed'));
    expect(mocks.openApp).not.toHaveBeenCalled();
    expect(screen.getByRole('button', { name: 'install' })).toBeEnabled();
  });

  it('does not let the click reach the card behind it', () => {
    seed([]);
    const onCard = vi.fn();
    render(
      <div role="presentation" onClick={onCard}>
        <ArtifactAppButton artifactId="art-1" name="weather.html" />
      </div>,
    );

    fireEvent.click(screen.getByRole('button', { name: 'install' }));

    expect(onCard).not.toHaveBeenCalled();
  });

  it('reads the registry once when it has not been read yet', () => {
    seed([], false);
    render(<ArtifactAppButton artifactId="art-1" name="weather.html" />);

    expect(mocks.refresh).toHaveBeenCalledTimes(1);
  });

  it('does not read the registry again when it is already loaded', () => {
    seed([], true);
    render(<ArtifactAppButton artifactId="art-1" name="weather.html" />);

    expect(mocks.refresh).not.toHaveBeenCalled();
  });
});
