/** @vitest-environment jsdom */
import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { SearchResultCard } from '@/components/features/app-shell/SearchResultCard';
import type { MockLocale } from '@/test-utils/realIntl';
import type { SearchResult } from '@/services/chatHistorySearch';

const intl = vi.hoisted(() => ({ locale: 'en' as MockLocale }));

vi.mock('next-intl', async () => (await import('@/test-utils/realIntl')).createRealIntlMock(() => intl.locale));

const hit = (role: string, snippet = 'the <mark>matched</mark> part'): SearchResult => ({
  id: 'm1',
  chat_id: 'c1',
  chat_title: 'Quarterly plan',
  role,
  content: 'the full message',
  snippet,
  sent_at: '2026-08-04T10:00:00.000Z',
});

function renderCard(role: string, snippet?: string) {
  return render(<SearchResultCard item={hit(role, snippet)} onOpen={vi.fn()} onCite={vi.fn()} />);
}

describe('SearchResultCard speaker label', () => {
  afterEach(() => {
    intl.locale = 'en';
  });

  it.each([
    ['en', 'user', 'You'],
    ['en', 'assistant', 'Assistant'],
    ['zh', 'user', '你'],
    ['zh', 'assistant', '助手'],
  ] as const)('in %s a %s message is labelled %s', (locale, role, label) => {
    intl.locale = locale;

    renderCard(role);

    expect(screen.getByText(label)).toBeTruthy();
    expect(screen.queryByText(role)).toBeNull();
  });
});

// The server escapes the message text and leaves only its own <mark> highlight tags; the card shows that as text.
describe('SearchResultCard snippet', () => {
  it('reads back the escaped text of the message and highlights the match', () => {
    const { container } = renderCard(
      'user',
      'Array&lt;Promise&lt;void&gt;&gt; <mark>keyword</mark> &amp; &quot;more&quot;',
    );

    expect(container.textContent).toContain('Array<Promise<void>> keyword & "more"');
    expect(container.querySelector('mark')?.textContent).toBe('keyword');
  });

  it('never turns the text of the message into elements', () => {
    const { container } = renderCard('user', 'see &lt;img src=x onerror=alert(1)&gt; then <mark>keyword</mark>');

    expect(container.querySelector('img')).toBeNull();
    expect(container.textContent).toContain('<img src=x onerror=alert(1)>');
  });
});

// The server writes `sent_at` with its UTC offset, so the card shows the day on the viewer's own calendar. Each pair
// straddles local midnight: a card that shows the UTC day, reads the stored time as local, or is an hour off in either
// direction gets one of each pair wrong.
describe('SearchResultCard date', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ['Asia/Shanghai', '2026-11-19T15:00:00.123456+00:00', 19],
    ['Asia/Shanghai', '2026-11-19T16:00:00.123456+00:00', 20],
    ['America/Los_Angeles', '2026-11-20T07:00:00.123456+00:00', 19],
    ['America/Los_Angeles', '2026-11-20T08:00:00.123456+00:00', 20],
    ['America/New_York', '2026-11-20T04:00:00.123456+00:00', 19],
    ['America/New_York', '2026-11-20T05:00:00.123456+00:00', 20],
  ] as const)('in %s a message sent at %s shows November %i', (timeZone, sentAt, day) => {
    vi.stubEnv('TZ', timeZone);

    render(<SearchResultCard item={{ ...hit('user'), sent_at: sentAt }} onOpen={vi.fn()} onCite={vi.fn()} />);

    // Built from the viewer's own calendar day, so the check holds in whatever locale the runner defaults to.
    const shown = new Date(2026, 10, day).toLocaleDateString(undefined, {
      month: 'short',
      day: 'numeric',
      year: 'numeric',
    });
    expect(screen.getByText(shown)).toBeTruthy();
  });
});
