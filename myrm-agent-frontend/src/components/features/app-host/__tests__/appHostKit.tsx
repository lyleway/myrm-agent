/**
 * [INPUT]
 * - vitest (POS: `vi.mock` here applies to every test file that imports this kit before `../AppHostView`)
 *
 * [OUTPUT]
 * - mocks / SHA / detail / pageResponds / resetAppHostMocks: the doubles and fixtures shared by the host page's tests.
 *
 * [POS]
 * Test kit of `AppHostView`: its collaborators (server client, desktop shell, dialogs, router, the sandboxed iframe) are
 * replaced here once, so the web and the desktop tests differ only in what they set up and assert.
 */

import { vi } from 'vitest';

const hoisted = vi.hoisted(() => ({
  push: vi.fn(),
  fetchAppDetail: vi.fn(),
  saveGrant: vi.fn(),
  uninstallApp: vi.fn(),
  listInstalledApps: vi.fn(),
  confirmAction: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
  htmlPreview: vi.fn(),
  isTauriRuntime: vi.fn(),
  closeAppWindow: vi.fn(),
  setAppWindowAlwaysOnTop: vi.fn(),
  setAppWindowTitle: vi.fn(),
  hasAppLauncher: vi.fn(),
  createAppLauncher: vi.fn(),
  removeAppLauncher: vi.fn(),
  releaseAppSurface: vi.fn(),
  ApiError: class extends Error {
    constructor(
      message: string,
      public code: number,
    ) {
      super(message);
    }
  },
}));

export const mocks = hoisted;

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: hoisted.push }) }));
const stableT = vi.hoisted(
  () => (key: string, values?: Record<string, string>) => (values?.name ? `${key}:${values.name}` : key),
);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('sonner', () => ({ toast: { success: hoisted.toastSuccess, error: hoisted.toastError } }));
vi.mock('@/lib/api', () => ({ ApiError: hoisted.ApiError, getStorageUrl: (uri: string) => `/storage/${uri}` }));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: hoisted.isTauriRuntime }));
vi.mock('@/lib/desktop/appWindow', () => ({
  closeAppWindow: hoisted.closeAppWindow,
  setAppWindowAlwaysOnTop: hoisted.setAppWindowAlwaysOnTop,
  setAppWindowTitle: hoisted.setAppWindowTitle,
}));
vi.mock('@/lib/desktop/appLauncher', () => ({
  hasAppLauncher: hoisted.hasAppLauncher,
  createAppLauncher: hoisted.createAppLauncher,
  removeAppLauncher: hoisted.removeAppLauncher,
}));
vi.mock('@/lib/desktop/appSurface', () => ({
  appSurface: {},
  releaseAppSurface: hoisted.releaseAppSurface,
  onAppSurfaceEvent: vi.fn().mockResolvedValue(() => undefined),
}));
vi.mock('@/lib/app-dialog', () => ({ confirmAction: hoisted.confirmAction }));
vi.mock('@/services/artifactApps', () => ({
  fetchAppDetail: hoisted.fetchAppDetail,
  saveGrant: hoisted.saveGrant,
  uninstallApp: hoisted.uninstallApp,
  listInstalledApps: hoisted.listInstalledApps,
  installApp: vi.fn(),
  proxyAppFetch: vi.fn(),
  reportAppActivity: vi.fn().mockResolvedValue(undefined),
  fetchAppActivity: vi.fn().mockResolvedValue([]),
}));
vi.mock('@/services/appNotification', () => ({
  showAppNotification: vi.fn(),
  requestAppNotificationPermission: vi.fn(),
}));
vi.mock('@/components/features/artifacts/renderers/MediaPreview', () => ({
  HtmlPreview: (props: { content: string; storageNamespace: string; chatId?: string }) => {
    hoisted.htmlPreview(props);
    return <iframe title="app" srcDoc={props.content} />;
  },
}));

export const SHA = 'f'.repeat(64);
export const PAGE = `<html><body><script type="application/json" id="myrm-app">{"notify": true, "fetch": ["api.weather.com"]}</script><h1>Weather</h1></body></html>`;

export const detail = (grants: unknown[] = []) => ({
  artifact_id: 'art-1',
  name: 'weather.html',
  chat_id: 'chat-1',
  latest_version: { id: 'v1', vault_uri: 'vault://o1', sha256_hash: SHA },
  grants,
});

export function pageResponds(html: string, ok = true) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok, text: async () => html }));
}

/** Fresh defaults for every test: a browser tab on `/apps/art-1` with a page that declares notify and fetch. */
export function resetAppHostMocks() {
  vi.clearAllMocks();
  mocks.saveGrant.mockImplementation(async (_id: string, capability: string, body: object) => ({
    capability,
    ...body,
  }));
  mocks.listInstalledApps.mockResolvedValue([]);
  mocks.isTauriRuntime.mockReturnValue(false);
  mocks.hasAppLauncher.mockResolvedValue(false);
  mocks.createAppLauncher.mockResolvedValue(true);
  mocks.removeAppLauncher.mockResolvedValue(true);
  mocks.releaseAppSurface.mockResolvedValue(undefined);
  mocks.setAppWindowTitle.mockResolvedValue(true);
  window.history.replaceState({}, '', '/apps/art-1');
  pageResponds(PAGE);
}
