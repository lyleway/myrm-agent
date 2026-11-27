/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ./appHostKit (POS: doubles and fixtures of the host page)
 * - ../AppHostView::AppHostView
 *
 * [OUTPUT]
 * - Tests: the launcher entry in the menu, and the app's own desktop window (full-height page, keep-on-top, window title, desktop permissions, closing instead of navigating).
 *
 * [POS]
 * Behaviour of `/apps/[artifactId]` in the desktop app: the app's own window, its launcher, and what leaves with the app.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SHA, detail, mocks, pageResponds, resetAppHostMocks } from './appHostKit';
import { AppHostView } from '../AppHostView';

describe('AppHostView in the desktop app', () => {
  beforeEach(resetAppHostMocks);

  describe('the launcher', () => {
    beforeEach(() => {
      mocks.isTauriRuntime.mockReturnValue(true);
      mocks.fetchAppDetail.mockResolvedValue(
        detail([
          { capability: 'notify', decision: 'allow', scope: [], version_sha256: SHA },
          { capability: 'fetch', decision: 'allow', scope: ['api.weather.com'], version_sha256: SHA },
        ]),
      );
    });

    async function openMenu() {
      await waitFor(() => expect(mocks.hasAppLauncher).toHaveBeenCalledWith('art-1'));
      fireEvent.pointerDown(screen.getByRole('button', { name: 'menu.label' }), { button: 0, ctrlKey: false });
    }

    it('is offered in the desktop app, unchecked while the app has none', async () => {
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      expect(await screen.findByRole('menuitemcheckbox', { name: 'launcher.menu' })).toHaveAttribute(
        'aria-checked',
        'false',
      );
    });

    it('adds the entry named after the app and says so', async () => {
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      fireEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'launcher.menu' }));

      await waitFor(() => expect(mocks.createAppLauncher).toHaveBeenCalledWith('art-1', 'weather.html'));
      expect(mocks.toastSuccess).toHaveBeenCalledWith('launcher.added');
    });

    it('removes an existing entry', async () => {
      mocks.hasAppLauncher.mockResolvedValue(true);
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      const item = await screen.findByRole('menuitemcheckbox', { name: 'launcher.menu' });
      await waitFor(() => expect(item).toHaveAttribute('aria-checked', 'true'));
      fireEvent.click(item);

      await waitFor(() => expect(mocks.removeAppLauncher).toHaveBeenCalledWith('art-1'));
      expect(mocks.toastSuccess).toHaveBeenCalledWith('launcher.removed');
    });

    it('reports a failure instead of pretending the entry exists', async () => {
      mocks.createAppLauncher.mockResolvedValue(false);
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      fireEvent.click(await screen.findByRole('menuitemcheckbox', { name: 'launcher.menu' }));

      await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('launcher.failed'));
      expect(mocks.toastSuccess).not.toHaveBeenCalled();
    });

    it('does not exist in a browser tab', async () => {
      mocks.isTauriRuntime.mockReturnValue(false);
      render(<AppHostView artifactId="art-1" />);
      await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());

      fireEvent.pointerDown(screen.getByRole('button', { name: 'menu.label' }), { button: 0, ctrlKey: false });

      await screen.findByRole('menuitem', { name: 'menu.permissions' });
      expect(screen.queryByRole('menuitemcheckbox', { name: 'launcher.menu' })).not.toBeInTheDocument();
      expect(mocks.hasAppLauncher).not.toHaveBeenCalled();
    });
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

    it('names the window after the app, since a launcher or link opens it before the name is known', async () => {
      render(<AppHostView artifactId="art-1" />);

      await waitFor(() => expect(mocks.setAppWindowTitle).toHaveBeenCalledWith('weather.html'));
    });

    it('asks about what the page wants to do on the desktop, in this window', async () => {
      mocks.fetchAppDetail.mockResolvedValue(detail());
      pageResponds(
        `<html><body><script type="application/json" id="myrm-app">{"tray": true, "shortcut": ["Alt+F1"]}</script></body></html>`,
      );
      render(<AppHostView artifactId="art-1" />);

      const dialog = await screen.findByRole('dialog');
      expect(within(dialog).getByText('tray.title')).toBeInTheDocument();
      expect(within(dialog).getByText('shortcut.title')).toBeInTheDocument();
    });

    it('takes the launcher and the tray item away with the app', async () => {
      mocks.confirmAction.mockResolvedValue(true);
      mocks.uninstallApp.mockResolvedValue(undefined);
      render(<AppHostView artifactId="art-1" />);
      await openMenu();

      fireEvent.click(await screen.findByRole('menuitem', { name: 'menu.uninstall' }));

      await waitFor(() => expect(mocks.closeAppWindow).toHaveBeenCalled());
      expect(mocks.removeAppLauncher).toHaveBeenCalledWith('art-1');
      expect(mocks.releaseAppSurface).toHaveBeenCalledTimes(1);
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
