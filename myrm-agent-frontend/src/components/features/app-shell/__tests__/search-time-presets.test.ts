import { describe, expect, it } from 'vitest';
import { computePresetRange, type TimePreset } from '../search-time-presets';

/** A local-time instant at 12:30:15.250, so a result that drifted by a day or lost its time of day cannot match. */
function at(year: number, month: number, day: number): Date {
  return new Date(year, month - 1, day, 12, 30, 15, 250);
}

function sinceOf(preset: TimePreset, now: Date): number {
  const { since } = computePresetRange(preset, now);
  if (!since) {
    throw new Error(`preset ${String(preset)} has no lower bound`);
  }
  return new Date(since).getTime();
}

describe('computePresetRange', () => {
  it('has no bounds without a preset', () => {
    expect(computePresetRange(null, at(2026, 11, 26))).toEqual({});
  });

  it('ends at the current moment', () => {
    const now = at(2026, 11, 26);
    expect(computePresetRange('week', now).until).toBe(now.toISOString());
  });

  it('starts today at local midnight', () => {
    expect(sinceOf('today', at(2026, 11, 26))).toBe(new Date(2026, 10, 26).getTime());
  });

  it('starts the past week seven days earlier at the same time', () => {
    expect(sinceOf('week', at(2026, 3, 3))).toBe(at(2026, 2, 24).getTime());
  });

  describe('past month', () => {
    it.each([
      ['an ordinary day', at(2026, 11, 26), at(2026, 10, 26)],
      ['May 31 stops at the last day of April', at(2026, 5, 31), at(2026, 4, 30)],
      ['Mar 31 stops at the last day of February', at(2026, 3, 31), at(2026, 2, 28)],
      ['Mar 31 of a leap year stops at Feb 29', at(2028, 3, 31), at(2028, 2, 29)],
      ['Oct 31 stops at the last day of September', at(2026, 10, 31), at(2026, 9, 30)],
      ['Jan 31 crosses into the previous year', at(2026, 1, 31), at(2025, 12, 31)],
    ])('%s', (_label, now, expected) => {
      expect(sinceOf('month', now)).toBe(expected.getTime());
    });
  });

  describe('past three months', () => {
    it.each([
      ['an ordinary day', at(2026, 11, 30), at(2026, 8, 30)],
      ['May 31 stops at the last day of February', at(2026, 5, 31), at(2026, 2, 28)],
      ['Dec 31 stops at the last day of September', at(2026, 12, 31), at(2026, 9, 30)],
      ['Aug 31 lands on the same day in May', at(2026, 8, 31), at(2026, 5, 31)],
      ['Mar 31 crosses into the previous year', at(2026, 3, 31), at(2025, 12, 31)],
      ['Feb 28 crosses into the previous year', at(2026, 2, 28), at(2025, 11, 28)],
    ])('%s', (_label, now, expected) => {
      expect(sinceOf('quarter', now)).toBe(expected.getTime());
    });
  });
});
