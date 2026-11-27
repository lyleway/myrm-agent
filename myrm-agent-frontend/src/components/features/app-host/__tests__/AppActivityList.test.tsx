/** @vitest-environment jsdom */

import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const fetchAppActivity = vi.hoisted(() => vi.fn());
vi.mock('@/services/artifactApps', () => ({ fetchAppActivity }));
const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));

import { AppActivityList } from '../AppActivityList';

const entry = (overrides: Record<string, unknown> = {}) => ({
  capability: 'fetch',
  outcome: 'ok',
  detail: 'api.weather.com',
  version_sha256: 'a'.repeat(64),
  created_at: new Date(Date.now() - 60_000).toISOString(),
  ...overrides,
});

describe('AppActivityList', () => {
  beforeEach(() => vi.clearAllMocks());

  it('asks for the newest 20 entries of the app and shows nothing under the title until they arrive', () => {
    fetchAppActivity.mockReturnValue(new Promise(() => undefined));

    render(<AppActivityList artifactId="art-1" />);

    expect(fetchAppActivity).toHaveBeenCalledWith('art-1', 20);
    expect(screen.getByRole('heading', { name: 'activity.title' })).toBeInTheDocument();
    expect(screen.queryByText('activity.empty')).not.toBeInTheDocument();
    expect(screen.queryByRole('list')).not.toBeInTheDocument();
  });

  it('says so when the app has done nothing yet', async () => {
    fetchAppActivity.mockResolvedValue([]);

    render(<AppActivityList artifactId="art-1" />);

    expect(await screen.findByText('activity.empty')).toBeInTheDocument();
  });

  it('lists what the app did with the site and outcome, and omits the site when there is none', async () => {
    fetchAppActivity.mockResolvedValue([
      entry(),
      entry({
        capability: 'notify',
        outcome: 'denied',
        detail: null,
        created_at: new Date(Date.now() - 3_600_000).toISOString(),
      }),
    ]);

    render(<AppActivityList artifactId="art-1" />);

    const rows = await screen.findAllByRole('listitem');
    expect(rows).toHaveLength(2);
    expect(rows[0]).toHaveTextContent('fetch.title');
    expect(rows[0]).toHaveTextContent('api.weather.com');
    expect(rows[0]).toHaveTextContent('activity.outcome.ok');
    expect(rows[1]).toHaveTextContent('notify.title');
    expect(rows[1]).toHaveTextContent('activity.outcome.denied');
    // The right-hand outcome span is always muted; the site span is only there when the call went to a site.
    expect(rows[0].querySelectorAll('span.text-muted-foreground')).toHaveLength(2);
    expect(rows[1].querySelectorAll('span.text-muted-foreground')).toHaveLength(1);
  });

  it("shows what a model call cost, and why a refused one was refused in the user's language", async () => {
    fetchAppActivity.mockResolvedValue([
      entry({ capability: 'ai', outcome: 'ok', detail: '$0.0020' }),
      entry({ capability: 'ai', outcome: 'denied', detail: 'budget_exceeded' }),
      entry({ capability: 'ai', outcome: 'denied', detail: 'risk_held' }),
    ]);

    render(<AppActivityList artifactId="art-1" />);

    const rows = await screen.findAllByRole('listitem');
    expect(rows[0]).toHaveTextContent('ai.title');
    expect(rows[0]).toHaveTextContent('$0.0020');
    expect(rows[1]).toHaveTextContent('activity.reason.budget_exceeded');
    expect(rows[2]).toHaveTextContent('activity.reason.risk_held');
  });

  it('treats a failed read as an empty log instead of an error', async () => {
    fetchAppActivity.mockRejectedValue(new Error('offline'));

    render(<AppActivityList artifactId="art-1" />);

    expect(await screen.findByText('activity.empty')).toBeInTheDocument();
  });

  it('ignores an answer that arrives after the list is gone or moved to another app', async () => {
    let resolveFirst: (rows: unknown[]) => void = () => undefined;
    fetchAppActivity
      .mockReturnValueOnce(new Promise((resolve) => (resolveFirst = resolve)))
      .mockResolvedValueOnce([entry({ detail: 'second.example.com' })]);

    const { rerender } = render(<AppActivityList artifactId="art-1" />);
    rerender(<AppActivityList artifactId="art-2" />);
    await waitFor(() => expect(screen.getByRole('listitem')).toHaveTextContent('second.example.com'));

    resolveFirst([entry({ detail: 'first.example.com' })]);
    await Promise.resolve();

    expect(screen.queryByText(/first\.example\.com/)).not.toBeInTheDocument();
    expect(screen.getByRole('listitem')).toHaveTextContent('second.example.com');
  });

  it('ignores a failure that arrives after the list is gone', async () => {
    let rejectFirst: (error: Error) => void = () => undefined;
    fetchAppActivity.mockReturnValueOnce(new Promise((_resolve, reject) => (rejectFirst = reject)));

    const { container, unmount } = render(<AppActivityList artifactId="art-1" />);
    unmount();
    rejectFirst(new Error('late'));
    await Promise.resolve();

    expect(container).toBeEmptyDOMElement();
  });
});
