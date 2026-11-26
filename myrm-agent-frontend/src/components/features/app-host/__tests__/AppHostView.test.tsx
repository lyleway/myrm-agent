/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../AppHostView::AppHostView
 *
 * [OUTPUT]
 * - Tests: loading states, the first-time permission question, the menu (permissions, uninstall), that the page is
 *   embedded with the client injected, and that it fills the window exactly when no shell is shown.
 *
 * [POS]
 * Behaviour of `/apps/[artifactId]`: what the user sees and which decisions and registry changes it leads to.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
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
  ApiError: class extends Error {
    constructor(
      message: string,
      public code: number,
    ) {
      super(message);
    }
  },
}));

vi.mock('next/navigation', () => ({ useRouter: () => ({ push: mocks.push }) }));
const stableT = vi.hoisted(
  () => (key: string, values?: Record<string, string>) => (values?.name ? `${key}:${values.name}` : key),
);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('sonner', () => ({ toast: { success: mocks.toastSuccess, error: mocks.toastError } }));
vi.mock('@/lib/api', () => ({ ApiError: mocks.ApiError, getStorageUrl: (uri: string) => `/storage/${uri}` }));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: mocks.isTauriRuntime }));
vi.mock('@/lib/desktop/appWindow', () => ({
  closeAppWindow: mocks.closeAppWindow,
  setAppWindowAlwaysOnTop: mocks.setAppWindowAlwaysOnTop,
}));
vi.mock('@/lib/app-dialog', () => ({ confirmAction: mocks.confirmAction }));
vi.mock('@/services/artifactApps', () => ({
  fetchAppDetail: mocks.fetchAppDetail,
  saveGrant: mocks.saveGrant,
  uninstallApp: mocks.uninstallApp,
  listInstalledApps: mocks.listInstalledApps,
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
    mocks.htmlPreview(props);
    return <iframe title="app" srcDoc={props.content} />;
  },
}));

import { AppHostView } from '../AppHostView';

const SHA = 'f'.repeat(64);
const PAGE = `<html><body><script type="application/json" id="myrm-app">{"notify": true, "fetch": ["api.weather.com"]}</script><h1>Weather</h1></body></html>`;

const detail = (grants: unknown[] = []) => ({
  artifact_id: 'art-1',
  name: 'weather.html',
  chat_id: 'chat-1',
  latest_version: { id: 'v1', vault_uri: 'vault://o1', sha256_hash: SHA },
  grants,
});

function pageResponds(html: string, ok = true) {
  vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok, text: async () => html }));
}

describe('AppHostView', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.saveGrant.mockImplementation(async (_id: string, capability: string, body: object) => ({
      capability,
      ...body,
    }));
    mocks.listInstalledApps.mockResolvedValue([]);
    mocks.isTauriRuntime.mockReturnValue(false);
    window.history.replaceState({}, '', '/apps/art-1');
    pageResponds(PAGE);
  });

  it('embeds the installed page with the client injected, bound to the app storage', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());

    render(<AppHostView artifactId="art-1" />);

    await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());
    const { content, storageNamespace, chatId } = mocks.htmlPreview.mock.calls.at(-1)?.[0] ?? {};
    expect(content.startsWith('<script>')).toBe(true);
    expect(content).toContain('window.myrm');
    expect(content).toContain('<h1>Weather</h1>');
    expect([storageNamespace, chatId]).toEqual(['art-1', 'chat-1']);
    expect(fetch).toHaveBeenCalledWith('/storage/vault://o1');
  });

  it('leaves room for the shell top bar while the shell is shown', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());

    render(<AppHostView artifactId="art-1" />);

    const root = await screen.findByTestId('app-host');
    expect(root).toHaveClass('h-[calc(100vh-4rem)]');
    expect(root).not.toHaveClass('h-dvh');
  });

  it('asks about the declared permissions the first time the app is opened', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());

    render(<AppHostView artifactId="art-1" />);

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('reviewTitle:weather.html')).toBeInTheDocument();
    expect(within(dialog).getByText('api.weather.com')).toBeInTheDocument();
  });

  it('does not ask again when the stored decisions already cover the declaration', async () => {
    mocks.fetchAppDetail.mockResolvedValue(
      detail([
        { capability: 'notify', decision: 'allow', scope: [], version_sha256: SHA },
        { capability: 'fetch', decision: 'deny', scope: ['api.weather.com'], version_sha256: SHA },
      ]),
    );

    render(<AppHostView artifactId="art-1" />);

    await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('undecided')).not.toBeInTheDocument();
  });

  it('asks again when the new version reaches a host the user never decided on', async () => {
    mocks.fetchAppDetail.mockResolvedValue(
      detail([
        { capability: 'notify', decision: 'allow', scope: [], version_sha256: 'old' },
        { capability: 'fetch', decision: 'allow', scope: ['other.example.com'], version_sha256: 'old' },
      ]),
    );

    render(<AppHostView artifactId="art-1" />);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
  });

  it('keeps a reminder after "not now" that reopens the question', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());
    render(<AppHostView artifactId="art-1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'later' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /undecided/ }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(mocks.saveGrant).not.toHaveBeenCalled();
  });

  it('stops reminding once the decisions are saved', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());
    render(<AppHostView artifactId="art-1" />);

    fireEvent.click(await screen.findByRole('button', { name: 'save' }));

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());
    expect(mocks.saveGrant).toHaveBeenCalledTimes(2);
    expect(screen.queryByRole('button', { name: /undecided/ })).not.toBeInTheDocument();
  });

  it('opens the standing permissions from the menu', async () => {
    mocks.fetchAppDetail.mockResolvedValue(
      detail([
        { capability: 'notify', decision: 'allow', scope: [], version_sha256: SHA },
        { capability: 'fetch', decision: 'allow', scope: ['api.weather.com'], version_sha256: SHA },
      ]),
    );
    render(<AppHostView artifactId="art-1" />);
    await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());

    fireEvent.pointerDown(screen.getByRole('button', { name: 'menu.label' }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'menu.permissions' }));

    const dialog = await screen.findByRole('dialog');
    expect(within(dialog).getByText('manageTitle')).toBeInTheDocument();
  });

  it('uninstalls after confirmation and leaves the page', async () => {
    mocks.fetchAppDetail.mockResolvedValue(
      detail([
        { capability: 'notify', decision: 'allow', scope: [], version_sha256: SHA },
        { capability: 'fetch', decision: 'allow', scope: ['api.weather.com'], version_sha256: SHA },
      ]),
    );
    mocks.confirmAction.mockResolvedValue(true);
    mocks.uninstallApp.mockResolvedValue(undefined);
    render(<AppHostView artifactId="art-1" />);
    await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());

    fireEvent.pointerDown(screen.getByRole('button', { name: 'menu.label' }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'menu.uninstall' }));

    await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/artifacts'));
    expect(mocks.confirmAction).toHaveBeenCalledWith(expect.objectContaining({ variant: 'destructive' }));
    expect(mocks.uninstallApp).toHaveBeenCalledWith('art-1');
    expect(mocks.toastSuccess).toHaveBeenCalledWith('uninstall.done');
  });

  it('keeps the app when the uninstall is not confirmed', async () => {
    mocks.fetchAppDetail.mockResolvedValue(
      detail([
        { capability: 'notify', decision: 'allow', scope: [], version_sha256: SHA },
        { capability: 'fetch', decision: 'allow', scope: ['api.weather.com'], version_sha256: SHA },
      ]),
    );
    mocks.confirmAction.mockResolvedValue(false);
    render(<AppHostView artifactId="art-1" />);
    await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());

    fireEvent.pointerDown(screen.getByRole('button', { name: 'menu.label' }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'menu.uninstall' }));

    await waitFor(() => expect(mocks.confirmAction).toHaveBeenCalled());
    expect(mocks.uninstallApp).not.toHaveBeenCalled();
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('reports a failed uninstall and stays on the page', async () => {
    mocks.fetchAppDetail.mockResolvedValue(
      detail([
        { capability: 'notify', decision: 'allow', scope: [], version_sha256: SHA },
        { capability: 'fetch', decision: 'allow', scope: ['api.weather.com'], version_sha256: SHA },
      ]),
    );
    mocks.confirmAction.mockResolvedValue(true);
    mocks.uninstallApp.mockRejectedValue(new Error('offline'));
    render(<AppHostView artifactId="art-1" />);
    await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());

    fireEvent.pointerDown(screen.getByRole('button', { name: 'menu.label' }), { button: 0, ctrlKey: false });
    fireEvent.click(await screen.findByRole('menuitem', { name: 'menu.uninstall' }));

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('uninstall.failed'));
    expect(mocks.push).not.toHaveBeenCalled();
  });

  it('says the app is not installed on a 404 and offers the way back', async () => {
    mocks.fetchAppDetail.mockRejectedValue(new mocks.ApiError('App not installed', 404));

    render(<AppHostView artifactId="art-1" />);

    expect(await screen.findByText('notInstalled.title')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'backToApps' }));
    expect(mocks.push).toHaveBeenCalledWith('/artifacts');
    expect(mocks.htmlPreview).not.toHaveBeenCalled();
  });

  it('says it could not open the app when the server fails', async () => {
    mocks.fetchAppDetail.mockRejectedValue(new mocks.ApiError('boom', 500));

    render(<AppHostView artifactId="art-1" />);

    expect(await screen.findByText('loadFailed.title')).toBeInTheDocument();
  });

  it('says it could not open the app when the page file cannot be read', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());
    pageResponds('', false);

    render(<AppHostView artifactId="art-1" />);

    expect(await screen.findByText('loadFailed.title')).toBeInTheDocument();
    expect(mocks.htmlPreview).not.toHaveBeenCalled();
  });

  describe('in its own desktop window', () => {
    const ALLOWED = [
      { capability: 'notify', decision: 'allow', scope: [], version_sha256: SHA },
      { capability: 'fetch', decision: 'allow', scope: ['api.weather.com'], version_sha256: SHA },
    ];

    async function openMenu() {
      await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());
      fireEvent.pointerDown(screen.getByRole('button', { name: 'menu.label' }), { button: 0, ctrlKey: false });
    }

    beforeEach(() => {
      mocks.isTauriRuntime.mockReturnValue(true);
      window.history.replaceState({}, '', '/apps/art-1?mode=focused');
      mocks.fetchAppDetail.mockResolvedValue(detail(ALLOWED));
      mocks.closeAppWindow.mockResolvedValue(true);
      mocks.setAppWindowAlwaysOnTop.mockResolvedValue(true);
    });

    it('fills the whole window since no shell sits above it', async () => {
      render(<AppHostView artifactId="art-1" />);

      const root = await screen.findByTestId('app-host');
      await waitFor(() => expect(root).toHaveClass('h-dvh'));
      expect(root).not.toHaveClass('h-[calc(100vh-4rem)]');
    });

    it('offers keep-on-top, and applies the choice to the window', async () => {
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      const item = await screen.findByRole('menuitemcheckbox', { name: 'keepOnTop' });
      expect(item).toHaveAttribute('aria-checked', 'false');
      fireEvent.click(item);

      await waitFor(() => expect(mocks.setAppWindowAlwaysOnTop).toHaveBeenCalledWith(true));
    });

    it('shows the new state only after the window accepted it, and reports a refusal', async () => {
      mocks.setAppWindowAlwaysOnTop.mockResolvedValue(false);
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      fireEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'keepOnTop' }));

      await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('keepOnTopFailed'));
      await openMenu();
      expect(await screen.findByRole('menuitemcheckbox', { name: 'keepOnTop' })).toHaveAttribute(
        'aria-checked',
        'false',
      );
    });

    it('closes the window instead of navigating after uninstall', async () => {
      mocks.confirmAction.mockResolvedValue(true);
      mocks.uninstallApp.mockResolvedValue(undefined);
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      fireEvent.click(await screen.findByRole('menuitem', { name: 'menu.uninstall' }));

      await waitFor(() => expect(mocks.closeAppWindow).toHaveBeenCalledTimes(1));
      expect(mocks.push).not.toHaveBeenCalled();
    });

    it('falls back to the artifacts page when the window cannot be closed', async () => {
      mocks.confirmAction.mockResolvedValue(true);
      mocks.uninstallApp.mockResolvedValue(undefined);
      mocks.closeAppWindow.mockResolvedValue(false);
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      fireEvent.click(await screen.findByRole('menuitem', { name: 'menu.uninstall' }));

      await waitFor(() => expect(mocks.push).toHaveBeenCalledWith('/artifacts'));
    });

    it('offers to close the window when the app is no longer installed', async () => {
      mocks.fetchAppDetail.mockRejectedValue(new mocks.ApiError('App not installed', 404));
      render(<AppHostView artifactId="art-1" />);

      fireEvent.click(await screen.findByRole('button', { name: 'closeWindow' }));

      await waitFor(() => expect(mocks.closeAppWindow).toHaveBeenCalled());
      expect(mocks.push).not.toHaveBeenCalled();
    });

    it('has no keep-on-top in a browser tab, even with the focused flag', async () => {
      mocks.isTauriRuntime.mockReturnValue(false);
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      await screen.findByRole('menuitem', { name: 'menu.permissions' });
      expect(screen.queryByRole('menuitemcheckbox', { name: 'keepOnTop' })).not.toBeInTheDocument();
    });
  });
});
