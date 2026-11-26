/**
 * [INPUT]
 * - @/services/artifactApps (POS: HTTP client of the installed-app API)
 *
 * [OUTPUT]
 * - useArtifactAppsStore: the installed apps, shared by the Artifacts page list and the install button on artifact cards.
 *
 * [POS]
 * Single client-side copy of the install registry so a card and the apps list never disagree about what is installed.
 * `refresh` is the only way the list is read; install and uninstall refresh it themselves.
 */

import { create } from 'zustand';
import {
  installApp,
  listInstalledApps,
  uninstallApp,
  type AppDetail,
  type InstalledApp,
} from '@/services/artifactApps';

interface ArtifactAppsState {
  apps: InstalledApp[];
  loaded: boolean;
  refresh: () => Promise<void>;
  install: (artifactId: string) => Promise<AppDetail>;
  uninstall: (artifactId: string) => Promise<void>;
}

/** Every HTML artifact card asks for the registry on mount; concurrent askers share one request. */
let inflightRefresh: Promise<void> | null = null;

/** A read already in flight started before the change and may miss it, so wait for it and read again. */
async function refreshAfterChange(refresh: () => Promise<void>): Promise<void> {
  await inflightRefresh;
  await refresh();
}

export const useArtifactAppsStore = create<ArtifactAppsState>((set, get) => ({
  apps: [],
  loaded: false,
  refresh: () => {
    inflightRefresh ??= listInstalledApps()
      .then((apps) => set({ apps, loaded: true }))
      .catch(() => set({ loaded: true }))
      .finally(() => {
        inflightRefresh = null;
      });
    return inflightRefresh;
  },
  install: async (artifactId) => {
    const detail = await installApp(artifactId);
    await refreshAfterChange(get().refresh);
    return detail;
  },
  uninstall: async (artifactId) => {
    await uninstallApp(artifactId);
    await refreshAfterChange(get().refresh);
  },
}));

export function selectIsInstalled(artifactId: string) {
  return (state: ArtifactAppsState): boolean => state.apps.some((app) => app.artifact_id === artifactId);
}
