import { beforeEach, describe, expect, it, vi } from 'vitest';
import { saveDiscordCredentials } from '@/services/channels/providersMessaging';

const mocks = vi.hoisted(() => ({
  cpChannelRequest: vi.fn(),
}));

vi.mock('@/lib/deploy-mode', () => ({
  isSandbox: () => true,
}));

vi.mock('@/lib/api', () => ({
  apiRequest: vi.fn(),
}));

vi.mock('@/services/channels/core', async (importOriginal) => {
  const actual = (await importOriginal()) as Record<string, unknown>;
  return { ...actual, cpChannelRequest: (...args: unknown[]) => mocks.cpChannelRequest(...args) };
});

describe('saveDiscordCredentials on the hosted service', () => {
  beforeEach(() => {
    mocks.cpChannelRequest.mockReset().mockResolvedValue({});
  });

  it('sends the bot token and the gateway switch, which is all the hosted service takes', async () => {
    await saveDiscordCredentials({ botToken: 'bot-token', botPolicy: 'mention_only', enableGateway: false });

    expect(mocks.cpChannelRequest).toHaveBeenCalledTimes(1);
    const [path, init] = mocks.cpChannelRequest.mock.calls[0] as [string, RequestInit];
    expect(path).toBe('/api/channels/credentials/discord');
    expect(init.method).toBe('PUT');
    expect(JSON.parse(String(init.body))).toEqual({ bot_token: 'bot-token', enable_gateway: false });
  });

  it('turns the gateway on when the switch was never touched', async () => {
    await saveDiscordCredentials({ botToken: 'bot-token', botPolicy: 'mention_only' });

    const [, init] = mocks.cpChannelRequest.mock.calls[0] as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({ bot_token: 'bot-token', enable_gateway: true });
  });
});
