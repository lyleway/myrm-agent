/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ./appHostKit (POS: doubles and fixtures of the host page)
 * - ../AppHostView::AppHostView
 *
 * [OUTPUT]
 * - Tests: loading states, the permission question on open, the menu (permissions, uninstall), that the page is embedded with the client injected, and that desktop capabilities are not offered where nothing could act on them.
 *
 * [POS]
 * Behaviour of `/apps/[artifactId]` in a browser tab: what the user sees and which decisions and registry changes it leads to.
 */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SHA, detail, mocks, pageResponds, resetAppHostMocks } from './appHostKit';
import { AppHostView } from '../AppHostView';

describe('AppHostView', () => {
  beforeEach(resetAppHostMocks);

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

  it('asks about the declared permissions when the app is opened with nothing decided', async () => {
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

  it('keeps asking after "not now": a reminder on the page, the question again on the next visit', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());
    const { unmount } = render(<AppHostView artifactId="art-1" />);
    fireEvent.click(await screen.findByRole('button', { name: 'later' }));
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument());

    fireEvent.click(screen.getByRole('button', { name: /undecided/ }));

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
    expect(mocks.saveGrant).not.toHaveBeenCalled();

    unmount();
    render(<AppHostView artifactId="art-1" />);

    expect(await screen.findByRole('dialog')).toBeInTheDocument();
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

  it('does not ask about desktop capabilities in a browser tab, where nothing could act on them', async () => {
    mocks.fetchAppDetail.mockResolvedValue(detail());
    pageResponds(`<html><body><script type="application/json" id="myrm-app">{"tray": true, "float": true}</script></body></html>`);

    render(<AppHostView artifactId="art-1" />);

    await waitFor(() => expect(mocks.htmlPreview).toHaveBeenCalled());
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(screen.queryByText('undecided')).not.toBeInTheDocument();
    expect(mocks.setAppWindowTitle).not.toHaveBeenCalled();
  });
});
