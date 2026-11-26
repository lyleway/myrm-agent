/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../ArtifactCard::ArtifactCard
 *
 * [OUTPUT]
 * - Tests: the "install as app" action appears on HTML artifacts only, bound to that artifact.
 *
 * [POS]
 * Only a self-contained page can be installed; offering the action on other types would promise something that fails.
 */

import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('@/lib/api', () => ({
  apiRequest: vi.fn().mockResolvedValue({}),
  getApiUrl: () => 'http://localhost',
  getStorageUrl: (url: string) => url,
}));
vi.mock('@/store/useChatStore', () => {
  const state = { chatId: 'chat-1', workspaceDir: null, agentConfig: null };
  const hook = (selector: (s: typeof state) => unknown) => selector(state);
  return { default: Object.assign(hook, { getState: () => state }) };
});
vi.mock('../card/ArtifactAppButton', () => ({
  ArtifactAppButton: (props: { artifactId: string; name: string }) => (
    <button aria-label="install app" data-testid="app-button" data-artifact={props.artifactId} data-name={props.name} />
  ),
}));

import ArtifactCard from '../ArtifactCard';

const artifactOf = (type: string, filename: string) =>
  ({ id: 'art-1', type, filename, content: '<p/>', size: 10 }) as never;

describe('ArtifactCard app action', () => {
  beforeEach(() => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue({ ok: false, json: async () => ({}) }));
  });

  it('is offered for an HTML artifact, bound to its id and name', () => {
    render(<ArtifactCard artifact={artifactOf('html', 'weather.html')} />);

    expect(screen.getByTestId('app-button')).toHaveAttribute('data-artifact', 'art-1');
    expect(screen.getByTestId('app-button')).toHaveAttribute('data-name', 'weather.html');
  });

  it.each([
    ['svg', 'a.svg'],
    ['markdown', 'a.md'],
    ['code', 'a.py'],
  ])('is not offered for a %s artifact', (type, filename) => {
    render(<ArtifactCard artifact={artifactOf(type, filename)} />);

    expect(screen.queryByTestId('app-button')).not.toBeInTheDocument();
  });
});
