/** @vitest-environment jsdom */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const stableT = Object.assign((key: string) => key, { has: () => false });

vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
}));

vi.mock('sonner', () => ({
  toast: { success: vi.fn(), error: vi.fn() },
}));

const mocks = vi.hoisted(() => ({
  getDiscordCredentials: vi.fn(),
  saveDiscordCredentials: vi.fn(),
  testDiscordConnection: vi.fn(),
}));

vi.mock('@/lib/deploy-mode', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, isSandbox: () => true };
});

vi.mock('@/services/channels', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return {
    ...actual,
    getDiscordCredentials: (...args: unknown[]) => mocks.getDiscordCredentials(...args),
    saveDiscordCredentials: (...args: unknown[]) => mocks.saveDiscordCredentials(...args),
    testDiscordConnection: (...args: unknown[]) => mocks.testDiscordConnection(...args),
  };
});

import { DiscordConfigCard } from '../DiscordConfigCard';

const SAVED = { botToken: 'saved-token', botPolicy: 'mention_only', enableGateway: true };

describe('DiscordConfigCard in sandbox mode', () => {
  beforeEach(() => {
    mocks.getDiscordCredentials.mockReset().mockResolvedValue(SAVED);
    mocks.saveDiscordCredentials.mockReset().mockResolvedValue(undefined);
    mocks.testDiscordConnection.mockReset().mockResolvedValue({ ok: true, message: '' });
  });

  it('asks for the bot token only', async () => {
    render(<DiscordConfigCard />);

    expect(await screen.findByLabelText('discordBotToken')).toHaveValue('saved-token');
    expect(screen.queryByLabelText('discordApplicationId')).not.toBeInTheDocument();
    expect(screen.queryByLabelText('discordPublicKey')).not.toBeInTheDocument();
  });

  it('treats a saved bot token as a complete configuration and checks the connection with it', async () => {
    render(<DiscordConfigCard />);

    await waitFor(() => expect(mocks.testDiscordConnection).toHaveBeenCalledWith('saved-token'));
  });

  it('saves the token the user typed', async () => {
    render(<DiscordConfigCard />);
    const token = await screen.findByLabelText('discordBotToken');

    fireEvent.change(token, { target: { value: 'new-token' } });
    fireEvent.click(screen.getByRole('button', { name: /discordSave/ }));

    await waitFor(() =>
      expect(mocks.saveDiscordCredentials).toHaveBeenCalledWith(expect.objectContaining({ botToken: 'new-token' })),
    );
  });
});
