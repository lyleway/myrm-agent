/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ./artifactCardHarness (POS: fake backend and collaborators)
 * - ../ArtifactCard::ArtifactCard
 *
 * [OUTPUT]
 * - Tests: the card's publish flow and its action buttons.
 *
 * [POS]
 * What publishing does to the card (dialog, live link, server hydration, publications) and what each action button
 * sends to the chat, the knowledge base, the clipboard and the download callbacks.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import {
  artifactOf,
  deployable,
  env,
  fetchMock,
  HYDRATE_URL,
  installArtifactCardHarness,
  PAGE_URL,
  PREFLIGHT_URL,
  PUBLICATION,
  SECOND_PUBLICATION,
} from './artifactCardHarness';
import ArtifactCard from '../ArtifactCard';

installArtifactCardHarness();

describe('ArtifactCard publishing', () => {
  it('refuses to open the publish dialog and says why when the artifact cannot be published', async () => {
    deployable(false);
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(await screen.findByTitle('h'));

    expect(env.toast.error).toHaveBeenCalledWith('m', { description: 'h' });
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'false');
  });

  it('opens the publish dialog when the artifact can be published', async () => {
    deployable(true);
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(await screen.findByTitle('publish.openModal'));

    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'true');
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-target', '');
  });

  it('treats an unreachable preflight as unknown: the button looks unready, yet the dialog still opens', async () => {
    env.routes.set(PREFLIGHT_URL, { ok: false, throws: true });
    const unhandled = vi.fn();
    process.on('unhandledRejection', unhandled);
    try {
      render(<ArtifactCard artifact={artifactOf()} />);
      await waitFor(() => expect(fetchMock.mock.calls.map(([url]) => url)).toContain(PREFLIGHT_URL));
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 0));
      });

      const deploy = screen.getByTitle('publish.openModal');
      expect(deploy).toHaveClass('opacity-60');
      fireEvent.click(deploy);

      expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'true');
      expect(env.toast.error).not.toHaveBeenCalled();
      expect(unhandled).not.toHaveBeenCalled();
    } finally {
      process.off('unhandledRejection', unhandled);
    }
  });

  it('opens the dialog on a given target to republish, and forgets the target when closed', () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByText('redeploy'));
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'true');
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-target', 't1');
    fireEvent.click(screen.getByText('close-modal'));

    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'false');
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-target', '');
  });

  it('shows the live link of a published artifact and opens it', async () => {
    deployable(true);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    render(<ArtifactCard artifact={artifactOf({ publications: [PUBLICATION] })} />);

    fireEvent.click(await screen.findByTitle('deploy.deployedLabel'));

    expect(open).toHaveBeenCalledWith('https://live.example.com/a', '_blank');
  });

  it('takes the publications and latest version from the server', async () => {
    env.routes.set(HYDRATE_URL, { ok: true, json: { latest_version_id: 'v5', publications: [PUBLICATION] } });

    render(<ArtifactCard artifact={artifactOf()} />);

    await waitFor(() => expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '1'));
    expect(screen.getByTestId('banners')).toHaveAttribute('data-latest', 'v5');
    expect(env.chat.updateMessages).toHaveBeenCalledTimes(1);
  });

  it('does not rewrite the chat when the server reports what the card already shows', async () => {
    env.routes.set(HYDRATE_URL, { ok: true, json: { publications: [PUBLICATION] } });

    render(<ArtifactCard artifact={artifactOf({ publications: [PUBLICATION] })} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(HYDRATE_URL));
    await act(async () => {});
    expect(env.chat.updateMessages).not.toHaveBeenCalled();
  });

  it('records a publication made in the dialog', async () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByText('publish-done'));

    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '1');
    expect(screen.getByTestId('banners')).toHaveAttribute('data-latest', 'v9');
    expect(env.chat.updateMessages).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(env.tryMarkMilestone).toHaveBeenCalledWith('first_multistep_delivery'));
  });

  it('follows a changed artifact, and keeps the publications it knew when the new one has none', () => {
    const { rerender } = render(<ArtifactCard artifact={artifactOf({ publications: [PUBLICATION] })} />);
    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '1');

    rerender(
      <ArtifactCard
        artifact={artifactOf({ filename: 'new.html', publications: [PUBLICATION, SECOND_PUBLICATION] })}
      />,
    );
    expect(screen.getByRole('heading', { name: 'new.html' })).toBeInTheDocument();
    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '2');

    rerender(<ArtifactCard artifact={artifactOf({ filename: 'newer.html', publications: [] })} />);
    expect(screen.getByRole('heading', { name: 'newer.html' })).toBeInTheDocument();
    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '2');
  });

  it('only checks publishing for artifacts that can be published', () => {
    render(<ArtifactCard artifact={artifactOf({ type: 'binary', filename: 'a.bin' })} />);

    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain(PREFLIGHT_URL);
    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain(HYDRATE_URL);
  });
});

describe('ArtifactCard actions', () => {
  it('copies the content and shows the confirmation', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: 'print(1)' });
    render(<ArtifactCard artifact={artifactOf({ type: 'code', filename: 'a.py' })} />);

    fireEvent.click(screen.getByTitle('copyCode'));

    await waitFor(() => expect(env.writeToClipboard).toHaveBeenCalledWith('print(1)'));
    expect(await screen.findByTitle('copied')).toBeInTheDocument();
  });

  it('attaches the artifact to the chat once', () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('insertToChat.title'));
    expect(env.chat.files).toEqual([
      expect.objectContaining({ fileName: 'page.html', fileUrl: PAGE_URL, fileType: 'uploaded' }),
    ]);
    expect(env.toast.success).toHaveBeenCalledWith('insertToChat.success');

    fireEvent.click(screen.getByTitle('insertToChat.title'));
    expect(env.chat.files).toHaveLength(1);
    expect(env.toast.info).toHaveBeenCalledWith('insertToChat.alreadyAttached');
  });

  it('adds the artifact to the knowledge base for the chat agent, and reports the outcome', async () => {
    env.ingestArtifact.mockResolvedValueOnce({ success: true, message: 'ok' });
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('ingestToWiki.title'));

    await waitFor(() => expect(env.toast.success).toHaveBeenCalledWith('ingestToWiki.success', { description: 'ok' }));
    expect(env.ingestArtifact).toHaveBeenCalledWith('art-1', 'agent-7');

    env.ingestArtifact.mockResolvedValueOnce({ success: false, message: 'bad' });
    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    await waitFor(() =>
      expect(env.toast.error).toHaveBeenCalledWith('ingestToWiki.failed', { description: 'bad' }),
    );

    env.ingestArtifact.mockRejectedValueOnce(new Error('offline'));
    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    await waitFor(() => expect(env.toast.error).toHaveBeenLastCalledWith('ingestToWiki.failed'));
  });

  it('ignores a second knowledge-base click while the first is still running', async () => {
    let finish: (value: { success: boolean; message: string }) => void = () => undefined;
    env.ingestArtifact.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    expect(screen.getByTitle('ingestToWiki.title')).toBeDisabled();
    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    await act(async () => finish({ success: true, message: 'ok' }));

    expect(env.ingestArtifact).toHaveBeenCalledTimes(1);
    expect(screen.getByTitle('ingestToWiki.title')).toBeEnabled();
  });

  it('hands the artifact to the download and preview callbacks', () => {
    const onDownload = vi.fn();
    const onPreview = vi.fn();
    const artifact = artifactOf();
    render(<ArtifactCard artifact={artifact} onDownload={onDownload} onPreview={onPreview} />);

    fireEvent.click(screen.getByTitle('download'));
    fireEvent.click(screen.getByTitle('preview'));
    fireEvent.click(screen.getByRole('heading', { name: 'page.html' }));

    expect(onDownload).toHaveBeenCalledWith(artifact);
    expect(onPreview).toHaveBeenCalledTimes(2);
    expect(onPreview).toHaveBeenCalledWith(artifact);
  });

  it('opens the artifact in a new tab', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('openInNewTab'));

    expect(open).toHaveBeenCalledWith(PAGE_URL, '_blank', 'noopener,noreferrer');
  });
});
