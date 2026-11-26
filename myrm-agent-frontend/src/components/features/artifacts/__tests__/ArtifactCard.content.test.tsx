/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ./artifactCardHarness (POS: fake backend and collaborators)
 * - ../ArtifactCard::ArtifactCard
 *
 * [OUTPUT]
 * - Tests: what the card shows below its header: the inline preview, the organize plan and the WeChat draft panel.
 *
 * [POS]
 * Which requests the card makes to show content, and what the user sees while that content loads, fails or is large.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { artifactOf, env, installArtifactCardHarness, PAGE_URL, storageFetches } from './artifactCardHarness';
import ArtifactCard from '../ArtifactCard';
import { LARGE_FILE_THRESHOLD } from '@/lib/constants/artifact';

installArtifactCardHarness();

describe('ArtifactCard inline preview', () => {
  it('opens an HTML artifact inline and loads its page once', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '<p>hi</p>' });

    render(<ArtifactCard artifact={artifactOf()} />);

    expect(await screen.findByTestId('html-preview')).toHaveTextContent('<p>hi</p>');
    expect(storageFetches()).toEqual([PAGE_URL]);
  });

  it.each([
    ['svg', 'a.svg', 'svg-preview'],
    ['mermaid', 'a.mmd', 'mermaid-preview'],
  ])('renders a %s artifact with its own renderer', async (type, filename, testId) => {
    env.routes.set(PAGE_URL, { ok: true, text: 'BODY' });

    render(<ArtifactCard artifact={artifactOf({ type, filename })} />);

    expect(await screen.findByTestId(testId)).toHaveTextContent('BODY');
  });

  it('folds and unfolds without loading the page again', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '<p>hi</p>' });
    render(<ArtifactCard artifact={artifactOf()} />);
    await screen.findByTestId('html-preview');

    fireEvent.click(screen.getByTitle('inlinePreview.collapse'));
    expect(screen.queryByTestId('html-preview')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTitle('inlinePreview.expand'));

    expect(await screen.findByTestId('html-preview')).toHaveTextContent('<p>hi</p>');
    expect(storageFetches()).toEqual([PAGE_URL]);
  });

  it('says the page could not be loaded, and tries again when it is unfolded again', async () => {
    render(<ArtifactCard artifact={artifactOf()} />);
    expect(await screen.findByText('inlinePreview.loadError')).toBeInTheDocument();
    env.routes.set(PAGE_URL, { ok: true, text: '<p>back</p>' });

    fireEvent.click(screen.getByTitle('inlinePreview.collapse'));
    fireEvent.click(screen.getByTitle('inlinePreview.expand'));

    expect(await screen.findByTestId('html-preview')).toHaveTextContent('<p>back</p>');
    expect(storageFetches()).toHaveLength(2);
  });

  it('does not load a large page inline and offers the full-screen view instead', async () => {
    const onPreview = vi.fn();
    const artifact = artifactOf({ size: LARGE_FILE_THRESHOLD + 1 });
    render(<ArtifactCard artifact={artifact} onPreview={onPreview} />);

    expect(screen.getByText('inlinePreview.largeFileHint')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /inlinePreview.viewFullscreen/ }));

    expect(onPreview).toHaveBeenCalledWith(artifact);
    expect(storageFetches()).toEqual([]);
  });

  it('warms the content cache when the pointer rests on the card, not when it only passes', async () => {
    const { container } = render(<ArtifactCard artifact={artifactOf({ type: 'code', filename: 'a.py' })} />);
    const card = container.firstElementChild as HTMLElement;

    fireEvent.mouseEnter(card);
    fireEvent.mouseLeave(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350));
    });
    expect(storageFetches()).toEqual([]);

    env.routes.set('storage:/p/page.html', { ok: true, text: 'print(1)' });
    fireEvent.mouseEnter(card);
    await waitFor(() => expect(storageFetches()).toEqual([PAGE_URL]));
    expect(env.cache.get('art-1')).toBe('print(1)');
  });

  it('does not warm the content cache after the card is gone', async () => {
    const { container, unmount } = render(<ArtifactCard artifact={artifactOf({ type: 'code', filename: 'a.py' })} />);

    fireEvent.mouseEnter(container.firstElementChild as HTMLElement);
    unmount();
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350));
    });

    expect(storageFetches()).toEqual([]);
  });
});

describe('ArtifactCard organize plan', () => {
  const planArtifact = artifactOf({ type: 'code', filename: 'x.organize-plan.json' });

  it('opens the plan loaded from its file', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '{"moves":[]}' });
    render(<ArtifactCard artifact={planArtifact} />);

    fireEvent.click(screen.getByTitle('organizePlan.openPanel'));

    const panel = await screen.findByTestId('organize-panel');
    expect(panel).toHaveTextContent('{"moves":[]}');
    expect(panel).toHaveAttribute('data-workspace', '/ws');
  });

  it('says the plan is invalid when its file cannot be read, and reads it again on the next opening', async () => {
    render(<ArtifactCard artifact={planArtifact} />);
    const toggle = screen.getByTitle('organizePlan.openPanel');

    fireEvent.click(toggle);
    expect(await screen.findByText('organizePlan.invalidPlan')).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByText('organizePlan.invalidPlan')).not.toBeInTheDocument();
    env.routes.set(PAGE_URL, { ok: true, text: '{"moves":[1]}' });
    fireEvent.click(toggle);

    expect(await screen.findByTestId('organize-panel')).toHaveTextContent('{"moves":[1]}');
    expect(storageFetches()).toHaveLength(2);
  });

  it('keeps a loaded plan while the panel is closed and reopened', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '{"moves":[]}' });
    render(<ArtifactCard artifact={planArtifact} />);
    const toggle = screen.getByTitle('organizePlan.openPanel');
    fireEvent.click(toggle);
    await screen.findByTestId('organize-panel');

    fireEvent.click(toggle);
    fireEvent.click(toggle);

    expect(await screen.findByTestId('organize-panel')).toBeInTheDocument();
    expect(storageFetches()).toHaveLength(1);
  });
});

describe('ArtifactCard WeChat draft', () => {
  it('opens and closes the draft panel for an HTML file on disk', () => {
    render(<ArtifactCard artifact={artifactOf({ file_path: '/ws/page.html' })} />);

    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));
    expect(screen.getByTestId('wechat-panel')).toHaveAttribute('data-path', '/ws/page.html');
    fireEvent.click(screen.getByText('finish-draft'));
    expect(screen.queryByTestId('wechat-panel')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));
    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));
    expect(screen.queryByTestId('wechat-panel')).not.toBeInTheDocument();
  });

  it('gives the panel the card page loader', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '<p>hi</p>' });
    render(<ArtifactCard artifact={artifactOf({ file_path: '/ws/page.html' })} />);
    await waitFor(() => expect(env.cache.get('art-1')).toBe('<p>hi</p>'));
    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));

    fireEvent.click(screen.getByText('load-page'));

    expect(storageFetches()).toEqual([PAGE_URL]);
  });

  it('is not offered without a file on disk', () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    expect(screen.queryByTestId('wechat-draft-open-panel')).not.toBeInTheDocument();
  });
});
