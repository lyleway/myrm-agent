/** @vitest-environment jsdom */

import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('@/components/features/app-host/InstalledAppsList', () => ({
  InstalledAppsList: () => <div data-testid="installed-apps" />,
}));

import { ArtifactsCenter } from '../ArtifactsCenter';

const ARTIFACT = {
  id: 'a1',
  name: 'notes.html',
  description: 'Weekly notes',
  created_at: '2026-11-20T00:00:00Z',
  updated_at: '2026-11-25T00:00:00Z',
};

const version = (id: string, overrides: Record<string, unknown> = {}) => ({
  id,
  vault_uri: `vault://${id}-uri`,
  sha256_hash: `${id}`.padEnd(64, 'f'),
  creator_id: 'user',
  commit_message: `message ${id}`,
  created_at: '2026-11-25T10:00:00Z',
  ...overrides,
});

type Reply = { ok?: boolean; json?: unknown; reject?: boolean; pending?: boolean };

/** Routes the center's requests by URL; an unlisted URL is a test mistake and fails loudly. */
function backend(routes: Record<string, Reply>) {
  const fetchMock = vi.fn(async (url: string) => {
    const reply = routes[url];
    if (!reply) {
      throw new Error(`unexpected request ${url}`);
    }
    if (reply.pending) {
      return new Promise(() => undefined);
    }
    if (reply.reject) {
      throw new Error('network down');
    }
    return { ok: reply.ok ?? true, json: async () => reply.json };
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const LIST = '/api/v1/files/artifacts/';
const VERSIONS = '/api/v1/files/artifacts/a1/versions';
const verify = (versionId: string) => `/api/v1/files/artifacts/a1/verify/${versionId}`;

describe('ArtifactsCenter', () => {
  beforeEach(() => {
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    vi.restoreAllMocks();
  });

  it('shows a skeleton until the artifact list has been read', () => {
    backend({ [LIST]: { pending: true } });

    render(<ArtifactsCenter />);

    expect(screen.queryByText('title')).not.toBeInTheDocument();
  });

  it('puts the installed apps above the artifact list and invites a selection', async () => {
    backend({ [LIST]: { json: { artifacts: [ARTIFACT] } } });

    render(<ArtifactsCenter />);

    const apps = await screen.findByTestId('installed-apps');
    const artifact = screen.getByRole('button', { name: /notes\.html/ });
    expect(apps.compareDocumentPosition(artifact) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    expect(screen.getByText('select_prompt')).toBeInTheDocument();
  });

  it('shows the empty state when there is no artifact, also when the answer has no list', async () => {
    backend({ [LIST]: { json: {} } });

    render(<ArtifactsCenter />);

    expect(await screen.findByText('empty')).toBeInTheDocument();
    expect(screen.getByText('emptyDesc')).toBeInTheDocument();
  });

  it('falls back to the empty state and logs when the list cannot be read', async () => {
    backend({ [LIST]: { reject: true } });

    render(<ArtifactsCenter />);

    expect(await screen.findByText('empty')).toBeInTheDocument();
    expect(console.error).toHaveBeenCalledWith('Failed to fetch artifacts', expect.any(Error));
  });

  it('opens the version history of the chosen artifact, numbered down from the newest, with preview and download links', async () => {
    backend({
      [LIST]: { json: { artifacts: [ARTIFACT] } },
      [VERSIONS]: {
        json: {
          versions: [version('v2'), version('v1', { commit_message: '', sha256_hash: 'abcdef12'.padEnd(64, '0') })],
        },
      },
    });
    render(<ArtifactsCenter />);

    fireEvent.click(await screen.findByRole('button', { name: /notes\.html/ }));

    expect(await screen.findByText('Weekly notes')).toBeInTheDocument();
    expect(screen.getByText('version_history')).toBeInTheDocument();
    expect(screen.getByText('v2')).toBeInTheDocument();
    expect(screen.getByText('v1')).toBeInTheDocument();
    expect(screen.getByText('message v2')).toBeInTheDocument();
    expect(screen.getByText('auto_saved_version')).toBeInTheDocument();
    expect(screen.getByText('abcdef12...')).toBeInTheDocument();
    const [preview] = screen.getAllByRole('link', { name: /preview/ });
    expect(preview).toHaveAttribute('href', '/api/v1/files/vault/v2-uri/content');
    const [download] = screen.getAllByRole('link', { name: /download/ });
    expect(download).toHaveAttribute('href', '/api/v1/files/vault/v2-uri/content?download=1');
  });

  it('describes an artifact without a description and says while its versions are loading', async () => {
    backend({
      [LIST]: { json: { artifacts: [{ ...ARTIFACT, description: '' }] } },
      [VERSIONS]: { pending: true },
    });
    render(<ArtifactsCenter />);

    fireEvent.click(await screen.findByRole('button', { name: /notes\.html/ }));

    expect(await screen.findByText('no_desc')).toBeInTheDocument();
    expect(screen.getByText('loading_versions')).toBeInTheDocument();
  });

  it('keeps the selection and logs when the versions cannot be read', async () => {
    backend({ [LIST]: { json: { artifacts: [ARTIFACT] } }, [VERSIONS]: { reject: true } });
    render(<ArtifactsCenter />);

    fireEvent.click(await screen.findByRole('button', { name: /notes\.html/ }));

    await waitFor(() => expect(console.error).toHaveBeenCalledWith('Failed to fetch versions', expect.any(Error)));
    expect(screen.getByText('loading_versions')).toBeInTheDocument();
  });

  it('marks a version intact when the hash verifies', async () => {
    backend({
      [LIST]: { json: { artifacts: [ARTIFACT] } },
      [VERSIONS]: { json: { versions: [version('v1')] } },
      [verify('v1')]: { json: { is_valid: true } },
    });
    render(<ArtifactsCenter />);
    fireEvent.click(await screen.findByRole('button', { name: /notes\.html/ }));

    fireEvent.click(await screen.findByRole('button', { name: /verify_hash/ }));

    expect(await screen.findByText('integrity_ok')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /verify_hash/ })).not.toBeInTheDocument();
  });

  it('flags a version whose hash does not match, and one that could not be checked at all', async () => {
    backend({
      [LIST]: { json: { artifacts: [ARTIFACT] } },
      [VERSIONS]: { json: { versions: [version('v2'), version('v1')] } },
      [verify('v2')]: { json: { is_valid: false } },
      [verify('v1')]: { reject: true },
    });
    render(<ArtifactsCenter />);
    fireEvent.click(await screen.findByRole('button', { name: /notes\.html/ }));
    const buttons = await screen.findAllByRole('button', { name: /verify_hash/ });

    fireEvent.click(buttons[0]);
    await waitFor(() => expect(screen.getAllByText('integrity_failed')).toHaveLength(1));
    fireEvent.click(screen.getByRole('button', { name: /verify_hash/ }));

    await waitFor(() => expect(screen.getAllByText('integrity_failed')).toHaveLength(2));
    expect(console.error).toHaveBeenCalledWith('Verification failed', expect.any(Error));
  });

  it('shows the check as running and blocks a second click while a hash is being verified', async () => {
    backend({
      [LIST]: { json: { artifacts: [ARTIFACT] } },
      [VERSIONS]: { json: { versions: [version('v1')] } },
      [verify('v1')]: { pending: true },
    });
    render(<ArtifactsCenter />);
    fireEvent.click(await screen.findByRole('button', { name: /notes\.html/ }));

    fireEvent.click(await screen.findByRole('button', { name: /verify_hash/ }));

    const running = await screen.findByRole('button', { name: /verifying/ });
    expect(running).toBeDisabled();
    expect(within(running).queryByText('verify_hash')).not.toBeInTheDocument();
  });
});
