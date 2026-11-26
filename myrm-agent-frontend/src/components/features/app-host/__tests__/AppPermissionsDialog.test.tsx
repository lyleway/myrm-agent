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
import type { CapabilityStatus, Declaration } from '@/lib/app-bridge/declaration';
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
const BOTH: Declaration = { notify: true, fetchHosts: ['api.weather.com', 'b.example.org'] };

function renderDialog(
  options: {
    mode?: PermissionsMode;
    declaration?: Declaration;
    statuses?: Record<BridgeCapability, CapabilityStatus>;
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
      statuses={options.statuses ?? { notify: 'ask', fetch: 'ask' }}
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

  it('only shows and stores what the page declares', async () => {
    renderDialog({
      declaration: { notify: true, fetchHosts: [] },
      statuses: { notify: 'ask', fetch: 'undeclared' },
    });

    expect(screen.queryByText('fetch.title')).not.toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'save' }));

    await waitFor(() => expect(mocks.saveGrant).toHaveBeenCalledTimes(1));
    expect(mocks.saveGrant).toHaveBeenCalledWith('art-1', 'notify', expect.anything());
  });

  it('shows the standing decisions in manage mode: allowed on, denied off', () => {
    renderDialog({ mode: 'manage', statuses: { notify: 'allowed', fetch: 'denied' } });

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
      declaration: { notify: false, fetchHosts: [] },
      statuses: { notify: 'undeclared', fetch: 'undeclared' },
    });

    expect(screen.getByText('none')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'save' })).not.toBeInTheDocument();
  });
});
