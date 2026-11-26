import { afterEach, describe, expect, it, vi } from 'vitest';

import { formatSharedContextDate } from '../shared-context/useSharedContextPanel';

// The history search returns `sent_at` with its UTC offset, so the panel shows the month, day and clock time of the
// viewer's own zone. One instant read in three zones: a panel that shows the UTC time, or reads the stored time as
// local, gets the hour wrong in every one of them.
describe('formatSharedContextDate', () => {
  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it.each([
    ['Asia/Shanghai', 20, 7],
    ['America/Los_Angeles', 19, 15],
    ['America/New_York', 19, 18],
  ] as const)('in %s a message sent at 2026-11-19 23:30 UTC shows November %i at hour %i:30', (timeZone, day, hour) => {
    vi.stubEnv('TZ', timeZone);

    // Built from the viewer's own wall clock, so the check holds in whatever locale the runner defaults to.
    const shown = new Date(2026, 10, day, hour, 30).toLocaleString(undefined, {
      month: 'short',
      day: 'numeric',
      hour: '2-digit',
      minute: '2-digit',
    });
    expect(formatSharedContextDate('2026-11-19T23:30:00.123456+00:00')).toBe(shown);
  });
});
