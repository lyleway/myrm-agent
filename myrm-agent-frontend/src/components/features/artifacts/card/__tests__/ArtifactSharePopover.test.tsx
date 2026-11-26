/** @vitest-environment jsdom */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  createArtifactSharePreview: vi.fn(),
  buildPublicArtifactShareUrl: vi.fn((path: string) => `https://app.example.com${path}`),
  writeToClipboard: vi.fn(),
  toastSuccess: vi.fn(),
  toastError: vi.fn(),
}));

const stableT = vi.hoisted(
  () => (key: string, values?: Record<string, number>) => (values ? `${key}|${Object.values(values).join('|')}` : key),
);
vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: { success: mocks.toastSuccess, error: mocks.toastError } }));
vi.mock('@/lib/utils/clipboardUtils', () => ({ writeToClipboard: mocks.writeToClipboard }));
vi.mock('@/services/artifactShares', () => ({ buildPublicArtifactShareUrl: mocks.buildPublicArtifactShareUrl }));
vi.mock('../../artifactUtils', () => ({ createArtifactSharePreview: mocks.createArtifactSharePreview }));

import { ArtifactSharePopover } from '../ArtifactSharePopover';

const PREVIEW = { share_path: '/s/abc', share_url: null, expires_at: 0, password_protected: false };

function open() {
  const onCardClick = vi.fn();
  render(
    <div onClick={onCardClick} role="presentation">
      <ArtifactSharePopover artifactId="art-1" artifactType="html" />
    </div>,
  );
  fireEvent.click(screen.getByTitle('sharePreview.open'));
  return onCardClick;
}

const create = () => fireEvent.click(screen.getByRole('button', { name: 'sharePreview.createBtn' }));

describe('ArtifactSharePopover', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createArtifactSharePreview.mockResolvedValue(PREVIEW);
    mocks.writeToClipboard.mockResolvedValue(undefined);
  });

  it('opens the share form without selecting the card', () => {
    const onCardClick = open();

    expect(screen.getByText('sharePreview.title')).toBeInTheDocument();
    expect(onCardClick).not.toHaveBeenCalled();
  });

  it('creates a seven day link by default, copies it and confirms', async () => {
    open();

    create();

    await waitFor(() => expect(mocks.toastSuccess).toHaveBeenCalled());
    expect(mocks.createArtifactSharePreview).toHaveBeenCalledWith('art-1', 'html', { ttlDays: 7, password: undefined });
    expect(mocks.buildPublicArtifactShareUrl).toHaveBeenCalledWith('/s/abc');
    expect(mocks.writeToClipboard).toHaveBeenCalledWith('https://app.example.com/s/abc');
    expect(mocks.toastSuccess).toHaveBeenCalledWith('sharePreview.successTitle', {
      description: 'sharePreview.successDescription|7',
    });
    await waitFor(() => expect(screen.queryByText('sharePreview.title')).not.toBeInTheDocument());
  });

  it('sends the chosen lifetime and a trimmed password, copies the server address when it gives one', async () => {
    mocks.createArtifactSharePreview.mockResolvedValue({
      ...PREVIEW,
      share_url: 'https://share.example.com/x',
      password_protected: true,
    });
    open();

    fireEvent.change(screen.getByPlaceholderText('sharePreview.passwordPlaceholder'), {
      target: { value: '  secret  ' },
    });
    fireEvent.click(screen.getByRole('button', { name: 'sharePreview.ttlDays|30' }));
    create();

    await waitFor(() => expect(mocks.toastSuccess).toHaveBeenCalled());
    expect(mocks.createArtifactSharePreview).toHaveBeenCalledWith('art-1', 'html', { ttlDays: 30, password: 'secret' });
    expect(mocks.buildPublicArtifactShareUrl).not.toHaveBeenCalled();
    expect(mocks.writeToClipboard).toHaveBeenCalledWith('https://share.example.com/x');
    expect(mocks.toastSuccess).toHaveBeenCalledWith('sharePreview.successTitle', {
      description: 'sharePreview.successDescriptionProtected|30',
    });
  });

  it('starts the next link from the defaults again', async () => {
    open();
    fireEvent.change(screen.getByPlaceholderText('sharePreview.passwordPlaceholder'), { target: { value: 'secret' } });
    fireEvent.click(screen.getByRole('button', { name: 'sharePreview.ttlDays|14' }));
    create();
    await waitFor(() => expect(screen.queryByText('sharePreview.title')).not.toBeInTheDocument());

    fireEvent.click(screen.getByTitle('sharePreview.open'));

    expect(screen.getByPlaceholderText('sharePreview.passwordPlaceholder')).toHaveValue('');
    create();
    await waitFor(() => expect(mocks.createArtifactSharePreview).toHaveBeenCalledTimes(2));
    expect(mocks.createArtifactSharePreview).toHaveBeenLastCalledWith('art-1', 'html', {
      ttlDays: 7,
      password: undefined,
    });
  });

  it('says what the server said when it refuses, and keeps the form open', async () => {
    mocks.createArtifactSharePreview.mockRejectedValue(new Error('Share limit reached'));
    open();

    create();

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('Share limit reached'));
    expect(mocks.writeToClipboard).not.toHaveBeenCalled();
    expect(screen.getByText('sharePreview.title')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'sharePreview.createBtn' })).toBeEnabled();
  });

  it('falls back to a generic failure message when the error carries none', async () => {
    mocks.createArtifactSharePreview.mockRejectedValue('nope');
    open();

    create();

    await waitFor(() => expect(mocks.toastError).toHaveBeenCalledWith('sharePreview.failed'));
  });

  it('blocks a second request while the first one is running', async () => {
    mocks.createArtifactSharePreview.mockReturnValue(new Promise(() => undefined));
    open();

    create();

    const busy = await screen.findByRole('button', { name: 'sharePreview.creating' });
    expect(busy).toBeDisabled();
    fireEvent.click(busy);
    expect(mocks.createArtifactSharePreview).toHaveBeenCalledTimes(1);
  });

  it('keeps clicks inside the form from selecting the card', () => {
    const onCardClick = open();

    fireEvent.click(screen.getByText('sharePreview.title'));

    expect(onCardClick).not.toHaveBeenCalled();
  });
});
