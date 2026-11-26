import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  listInstalledApps: vi.fn(),
  installApp: vi.fn(),
  uninstallApp: vi.fn(),
}));

vi.mock('@/services/artifactApps', () => mocks);

import { selectIsInstalled, useArtifactAppsStore } from '../useArtifactAppsStore';

const app = (id: string) => ({
  artifact_id: id,
  name: `${id}.html`,
  installed_at: '2026-11-26T00:00:00',
  updated_at: '2026-11-26T00:00:00',
});

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe('useArtifactAppsStore', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useArtifactAppsStore.setState({ apps: [], loaded: false });
  });

  it('reads the registry once for many concurrent askers', async () => {
    mocks.listInstalledApps.mockResolvedValue([app('a')]);
    const { refresh } = useArtifactAppsStore.getState();

    await Promise.all([refresh(), refresh(), refresh()]);

    expect(mocks.listInstalledApps).toHaveBeenCalledTimes(1);
    expect(useArtifactAppsStore.getState()).toMatchObject({ apps: [app('a')], loaded: true });
  });

  it('marks the registry as read even when the read fails, keeping what it had', async () => {
    useArtifactAppsStore.setState({ apps: [app('kept')] });
    mocks.listInstalledApps.mockRejectedValue(new Error('offline'));

    await useArtifactAppsStore.getState().refresh();

    expect(useArtifactAppsStore.getState()).toMatchObject({ apps: [app('kept')], loaded: true });
  });

  it('reads again after a failed read instead of replaying the failure', async () => {
    mocks.listInstalledApps.mockRejectedValueOnce(new Error('offline')).mockResolvedValueOnce([app('b')]);

    await useArtifactAppsStore.getState().refresh();
    await useArtifactAppsStore.getState().refresh();

    expect(useArtifactAppsStore.getState().apps).toEqual([app('b')]);
  });

  it('shows an installed app after install, even when an older read was still in flight', async () => {
    const stale = deferred<ReturnType<typeof app>[]>();
    mocks.listInstalledApps.mockReturnValueOnce(stale.promise).mockResolvedValueOnce([app('new')]);
    mocks.installApp.mockResolvedValue({ artifact_id: 'new' });

    const earlier = useArtifactAppsStore.getState().refresh();
    const installing = useArtifactAppsStore.getState().install('new');
    stale.resolve([]);
    await Promise.all([earlier, installing]);

    expect(mocks.installApp).toHaveBeenCalledWith('new');
    expect(useArtifactAppsStore.getState().apps).toEqual([app('new')]);
  });

  it('drops an app after uninstall', async () => {
    useArtifactAppsStore.setState({ apps: [app('a'), app('b')], loaded: true });
    mocks.uninstallApp.mockResolvedValue(undefined);
    mocks.listInstalledApps.mockResolvedValue([app('b')]);

    await useArtifactAppsStore.getState().uninstall('a');

    expect(mocks.uninstallApp).toHaveBeenCalledWith('a');
    expect(useArtifactAppsStore.getState().apps).toEqual([app('b')]);
  });

  it('does not refresh when the install itself fails', async () => {
    mocks.installApp.mockRejectedValue(new Error('only html'));

    await expect(useArtifactAppsStore.getState().install('x')).rejects.toThrow('only html');

    expect(mocks.listInstalledApps).not.toHaveBeenCalled();
  });

  it('selects installation by artifact id', () => {
    useArtifactAppsStore.setState({ apps: [app('a')] });

    expect(selectIsInstalled('a')(useArtifactAppsStore.getState())).toBe(true);
    expect(selectIsInstalled('b')(useArtifactAppsStore.getState())).toBe(false);
  });
});
