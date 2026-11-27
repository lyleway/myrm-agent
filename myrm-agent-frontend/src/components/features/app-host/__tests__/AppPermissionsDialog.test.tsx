/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../AppPermissionsDialog::AppPermissionsDialog
 *
 * [OUTPUT]
 * - Tests: what is stored when the user saves (decision per capability, the scope shown, the version), review vs
 *   manage mode, and that nothing is stored when the user dismisses the question.
 *
 * [POS]
 * The dialog is the only place a decision is made; the scope it stores must be exactly what it displayed.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { EMPTY_DECLARATION, type CapabilityStatus, type Declaration } from '@/lib/app-bridge/declaration';
import type { ExternalScript } from '@/lib/app-bridge/externalScripts';
import type { BridgeCapability } from '@/lib/app-bridge/protocol';

const mocks = vi.hoisted(() => ({
  saveGrant: vi.fn(),
  requestAppNotificationPermission: vi.fn(),
  toastError: vi.fn(),
}));

const stableT = vi.hoisted(
  () => (key: string, values?: Record<string, string>) => (values?.name ? `${key}:${values.name}` : key),
);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }));
vi.mock('@/services/artifactApps', () => ({
  saveGrant: mocks.saveGrant,
  fetchAppActivity: vi.fn().mockResolvedValue([]),
}));
vi.mock('@/services/appNotification', () => ({
  requestAppNotificationPermission: mocks.requestAppNotificationPermission,
}));

import { AppPermissionsDialog, type PermissionsMode } from '../AppPermissionsDialog';

const SHA = 'a'.repeat(64);
const UNDECLARED: Record<BridgeCapability, CapabilityStatus> = {
  notify: 'undeclared',
  fetch: 'undeclared',
  ai: 'undeclared',
  tray: 'undeclared',
  shortcut: 'undeclared',
  float: 'undeclared',
};
const BOTH: Declaration = { ...EMPTY_DECLARATION, notify: true, fetchHosts: ['api.weather.com', 'b.example.org'] };
const ASK_BOTH: Record<BridgeCapability, CapabilityStatus> = {
  ...UNDECLARED,
  notify: 'ask',
  fetch: 'ask',
};
const DESKTOP: Declaration = {
  ...EMPTY_DECLARATION,
  tray: true,
  float: true,
  shortcuts: ['Alt+F1', 'CommandOrControl+Shift+K'],
};
const ASK_DESKTOP: Record<BridgeCapability, CapabilityStatus> = {
  ...UNDECLARED,
  tray: 'ask',
  shortcut: 'ask',
  float: 'ask',
};

function renderDialog(
  options: {
    mode?: PermissionsMode;
    declaration?: Declaration;
    statuses?: Record<BridgeCapability, CapabilityStatus>;
    desktopAvailable?: boolean;
    notFromConversation?: boolean;
    externalScripts?: ExternalScript[];
  } = {},
) {
  const onSaved = vi.fn();
  const onClose = vi.fn();
  render(
    <AppPermissionsDialog
      open
      mode={options.mode ?? 'review'}
      artifactId="art-1"
      appName="Weather"
      versionSha256={SHA}
      declaration={options.declaration ?? BOTH}
      statuses={options.statuses ?? ASK_BOTH}
      desktopAvailable={options.desktopAvailable ?? false}
      notFromConversation={options.notFromConversation ?? false}
      externalScripts={options.externalScripts ?? []}
      onSaved={onSaved}
      onClose={onClose}
    />,
  );
  return { onSaved, onClose };
}

const toggle = (name: string) => screen.getByRole('switch', { name });

describe('AppPermissionsDialog', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.saveGrant.mockImplementation(async (_id: string, capability: string, body: Record<string, unknown>) => ({
      capability,
      ...body,
    }));
  });

  it('names the app and lists each declared capability with the hosts it would reach', () => {
    renderDialog();

    expect(screen.getByText('reviewTitle:Weather')).toBeInTheDocument();
    expect(screen.getByText('notify.title')).toBeInTheDocument();
    expect(screen.getByText('fetch.title')).toBeInTheDocument();
    expect(screen.getByText('api.weather.com')).toBeInTheDocument();
    expect(screen.getByText('b.example.org')).toBeInTheDocument();
  });

  it('starts every undecided capability switched on and stores allow with the displayed scope and version', async () => {
    const { onSaved, onClose } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(onClose).toHaveBeenCalled());
    expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'notify', {
      decision: 'allow',
      scope: [],
      version_sha256: SHA,
    });
    expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'fetch', {
      decision: 'allow',
      scope: ['api.weather.com', 'b.example.org'],
      version_sha256: SHA,
    });
    expect(onSaved).toHaveBeenCalledWith([
      expect.objectContaining({ capability: 'notify', decision: 'allow' }),
      expect.objectContaining({ capability: 'fetch', decision: 'allow' }),
    ]);
  });

  it('stores deny for a capability the user switched off, with the scope the question showed', async () => {
    renderDialog();

    fireEvent.click(toggle('fetch.title'));
    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(mocks.saveGrant).toHaveBeenCalledTimes(2));
    expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'fetch', {
      decision: 'deny',
      scope: ['api.weather.com', 'b.example.org'],
      version_sha256: SHA,
    });
    expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'notify', expect.objectContaining({ decision: 'allow' }));
  });

  it('asks the OS for notification permission from the click that allows notify', async () => {
    renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(mocks.requestAppNotificationPermission).toHaveBeenCalledTimes(1));
  });

  it('does not ask the OS when notify stays off', async () => {
    renderDialog();

    fireEvent.click(toggle('notify.title'));
    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(mocks.saveGrant).toHaveBeenCalledTimes(2));
    expect(mocks.requestAppNotificationPermission).not.toHaveBeenCalled();
  });

  it('lists ai in every window and stores it with no scope', async () => {
    renderDialog({
      declaration: { ...EMPTY_DECLARATION, ai: true },
      statuses: { ...UNDECLARED, ai: 'ask' },
      desktopAvailable: false,
    });

    expect(screen.getByText('ai.title')).toBeInTheDocument();
    expect(screen.getByText('ai.destination')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(mocks.saveGrant).toHaveBeenCalledTimes(1));
    expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'ai', {
      decision: 'allow',
      scope: [],
      version_sha256: SHA,
    });
  });

  it('only shows and stores what the page declares', async () => {
    renderDialog({
      declaration: { ...EMPTY_DECLARATION, notify: true },
      statuses: { ...UNDECLARED, notify: 'ask' },
    });

    expect(screen.queryByText('fetch.title')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(mocks.saveGrant).toHaveBeenCalledTimes(1));
    expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'notify', expect.anything());
  });

  it('shows the standing decisions in manage mode: allowed on, denied off', () => {
    renderDialog({ mode: 'manage', statuses: { ...UNDECLARED, notify: 'allowed', fetch: 'denied' } });

    expect(screen.getByText('manageTitle')).toBeInTheDocument();
    expect(toggle('notify.title')).toBeChecked();
    expect(toggle('fetch.title')).not.toBeChecked();
    expect(screen.getByText('activity.title')).toBeInTheDocument();
  });

  it('does not show the activity list in the review question', () => {
    renderDialog({ mode: 'review' });

    expect(screen.queryByText('activity.title')).not.toBeInTheDocument();
  });

  it('stores nothing when the question is dismissed', () => {
    const { onClose } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'later' }));

    expect(onClose).toHaveBeenCalled();
    expect(mocks.saveGrant).not.toHaveBeenCalled();
  });

  it('tells the user when saving fails and keeps the dialog open', async () => {
    mocks.saveGrant.mockRejectedValue(new Error('offline'));
    const { onClose, onSaved } = renderDialog();

    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('saveFailed'));
    expect(onClose).not.toHaveBeenCalled();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it('says so when the page asks for nothing, with nothing to save', () => {
    renderDialog({
      mode: 'manage',
      declaration: EMPTY_DECLARATION,
      statuses: UNDECLARED,
    });

    expect(screen.getByText('none')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument();
  });

  describe('in the app’s own desktop window', () => {
    it('lists the desktop capabilities with the key combinations as people read them', () => {
      renderDialog({ declaration: DESKTOP, statuses: ASK_DESKTOP, desktopAvailable: true });

      expect(screen.getByText('tray.title')).toBeInTheDocument();
      expect(screen.getByText('shortcut.title')).toBeInTheDocument();
      expect(screen.getByText('float.title')).toBeInTheDocument();
      expect(screen.getByText('Alt+F1')).toBeInTheDocument();
      expect(screen.getByText('Cmd/Ctrl+Shift+K')).toBeInTheDocument();
    });

    it('stores each decision with the scope that was shown, key combinations included', async () => {
      const { onClose } = renderDialog({ declaration: DESKTOP, statuses: ASK_DESKTOP, desktopAvailable: true });

      fireEvent.click(toggle('float.title'));
      fireEvent.click(screen.getByRole('button', { name: 'save' }));

      await waitFor(() => expect(onClose).toHaveBeenCalled());
      expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'tray', {
        decision: 'allow',
        scope: [],
        version_sha256: SHA,
      });
      expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'float', {
        decision: 'deny',
        scope: [],
        version_sha256: SHA,
      });
      expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'shortcut', {
        decision: 'allow',
        scope: ['Alt+F1', 'CommandOrControl+Shift+K'],
        version_sha256: SHA,
      });
      expect(mocks.requestAppNotificationPermission).not.toHaveBeenCalled();
    });
  });

  describe('outside it', () => {
    it('neither lists nor stores what only the desktop window can do, and says why', async () => {
      renderDialog({
        declaration: { ...DESKTOP, notify: true },
        statuses: { ...ASK_DESKTOP, notify: 'ask' },
        desktopAvailable: false,
      });

      expect(screen.getByText('notify.title')).toBeInTheDocument();
      expect(screen.queryByText('tray.title')).not.toBeInTheDocument();
      expect(screen.queryByText('shortcut.title')).not.toBeInTheDocument();
      expect(screen.getByText('desktopOnly')).toBeInTheDocument();

      fireEvent.click(screen.getByRole('button', { name: 'save' }));
      await waitFor(() => expect(mocks.saveGrant).toHaveBeenCalledTimes(1));
      expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'notify', expect.anything());
    });

    it('explains an app that asks only for desktop features instead of showing an empty list', () => {
      renderDialog({ declaration: DESKTOP, statuses: ASK_DESKTOP, desktopAvailable: false });

      expect(screen.getByText('desktopOnly')).toBeInTheDocument();
      expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument();
    });
  });

  describe('what a permission cannot tell the user', () => {
    it('always says nobody reviewed the code', () => {
      renderDialog();

      expect(screen.getByText('unreviewed')).toBeInTheDocument();
      expect(screen.queryByTestId('app-note-imported')).not.toBeInTheDocument();
      expect(screen.queryByTestId('app-note-drift')).not.toBeInTheDocument();
    });

    it('warns when the file did not come out of one of the user’s conversations', () => {
      renderDialog({ notFromConversation: true });

      expect(screen.getByTestId('app-note-imported')).toBeInTheDocument();
    });

    it('lists the sites that serve scripts and warns about the ones that can change', () => {
      renderDialog({
        externalScripts: [
          { host: 'cdn.example.com', mayChange: false },
          { host: 'unpkg.com', mayChange: true },
        ],
      });

      expect(screen.getByText('scripts')).toBeInTheDocument();
      expect(screen.getByTestId('app-note-drift')).toBeInTheDocument();
    });

    it('stays quiet about drift when every script is locked', () => {
      renderDialog({ externalScripts: [{ host: 'cdn.example.com', mayChange: false }] });

      expect(screen.getByText('scripts')).toBeInTheDocument();
      expect(screen.queryByTestId('app-note-drift')).not.toBeInTheDocument();
    });
  });
});
