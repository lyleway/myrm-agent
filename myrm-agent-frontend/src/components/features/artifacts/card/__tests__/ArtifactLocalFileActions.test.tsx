/** @vitest-environment jsdom */

import { act, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  apiRequest: vi.fn(),
  writeToClipboard: vi.fn(),
  toastError: vi.fn(),
}));

const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }));
vi.mock('@/lib/api', () => ({ apiRequest: mocks.apiRequest }));
vi.mock('@/lib/utils/clipboardUtils', () => ({ writeToClipboard: mocks.writeToClipboard }));

import { ArtifactLocalFileActions } from '../ArtifactLocalFileActions';

function renderInCard() {
  const onCardClick = vi.fn();
  render(
    <div onClick={onCardClick} role="presentation">
      <ArtifactLocalFileActions artifactId="art-1" filePath="/ws/report.html" />
    </div>,
  );
  return onCardClick;
}

describe('ArtifactLocalFileActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.apiRequest.mockResolvedValue({});
    mocks.writeToClipboard.mockResolvedValue(undefined);
  });

  afterEach(() => vi.useRealTimers());

  it('opens the file with the default app without selecting the card', async () => {
    const onCardClick = renderInCard();

    await act(async () => fireEvent.click(screen.getByTitle('openWithDefaultApp')));

    expect(mocks.apiRequest).toHaveBeenCalledWith('/files/art-1/open', { method: 'POST' });
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('reveals the file in the file manager without selecting the card', async () => {
    const onCardClick = renderInCard();

    await act(async () => fireEvent.click(screen.getByTitle('revealInFileManager')));

    expect(mocks.apiRequest).toHaveBeenCalledWith('/files/art-1/reveal', { method: 'POST' });
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it.each([['openWithDefaultApp'], ['revealInFileManager']])('tells the user when %s fails', async (title) => {
    mocks.apiRequest.mockRejectedValue(new Error('boom'));
    renderInCard();

    await act(async () => fireEvent.click(screen.getByTitle(title)));

    expect(mocks.toastError).toHaveBeenCalledWith('errors.serverError');
  });

  it('copies the path, confirms it for two seconds, and does not select the card', async () => {
    vi.useFakeTimers();
    const onCardClick = renderInCard();

    await act(async () => fireEvent.click(screen.getByTitle('copyPath')));

    expect(mocks.writeToClipboard).toHaveBeenCalledWith('/ws/report.html');
    expect(screen.getByTitle('copied')).toBeInTheDocument();
    expect(onCardClick).not.toHaveBeenCalled();

    act(() => {
      vi.advanceTimersByTime(2000);
    });

    expect(screen.getByTitle('copyPath')).toBeInTheDocument();
  });
});
