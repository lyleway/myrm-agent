/**
 * [INPUT]
 * - ../callLimiter::createCallLimiter
 *
 * [OUTPUT]
 * - Tests: each capability is paced on its own, the first refusal of a window is told apart, the window slides.
 *
 * [POS]
 * Keeps a page from flooding the bridge: refused calls would otherwise fill the activity log one row per call.
 */

import { describe, expect, it } from 'vitest';
import { createCallLimiter } from '../callLimiter';

function clock(start = 1_000_000) {
  let time = start;
  return { now: () => time, advance: (ms: number) => (time += ms) };
}

describe('createCallLimiter', () => {
  it('admits up to the limit of the capability, then refuses and reports the first refusal once', () => {
    const limiter = createCallLimiter(clock().now);

    expect(Array.from({ length: 5 }, () => limiter('notify'))).toEqual(Array(5).fill('admit'));
    expect(limiter('notify')).toBe('first_over');
    expect(limiter('notify')).toBe('over');
    expect(limiter('notify')).toBe('over');
  });

  it('paces each capability on its own', () => {
    const limiter = createCallLimiter(clock().now);
    for (let i = 0; i < 5; i += 1) {
      limiter('notify');
    }

    expect(limiter('notify')).toBe('first_over');
    expect(limiter('fetch')).toBe('admit');
    expect(limiter('tray')).toBe('admit');
  });

  it('paces ai below fetch, on a budget of its own', () => {
    const limiter = createCallLimiter(clock().now);
    const outcomes = Array.from({ length: 41 }, () => limiter('ai'));

    expect(outcomes.slice(0, 40)).toEqual(Array(40).fill('admit'));
    expect(outcomes[40]).toBe('first_over');
    expect(limiter('fetch')).toBe('admit');
  });

  it('gives the desktop capabilities their own, higher limits', () => {
    const limiter = createCallLimiter(clock().now);
    const outcomes = (capability: Parameters<typeof limiter>[0], count: number) =>
      Array.from({ length: count }, () => limiter(capability));

    expect(outcomes('tray', 31).at(29)).toBe('admit');
    expect(outcomes('tray', 1)[0]).toBe('over');
    expect(outcomes('shortcut', 21).slice(0, 20)).toEqual(Array(20).fill('admit'));
    expect(outcomes('float', 31).slice(0, 30)).toEqual(Array(30).fill('admit'));
  });

  it('lets calls through again once the window has moved past them, and reports a new refusal as first', () => {
    const time = clock();
    const limiter = createCallLimiter(time.now);
    for (let i = 0; i < 5; i += 1) {
      limiter('notify');
    }
    expect(limiter('notify')).toBe('first_over');

    time.advance(60_001);

    expect(limiter('notify')).toBe('admit');
    for (let i = 0; i < 4; i += 1) {
      limiter('notify');
    }
    expect(limiter('notify')).toBe('first_over');
  });

  it('counts only the calls still inside the window', () => {
    const time = clock();
    const limiter = createCallLimiter(time.now);
    limiter('notify');
    limiter('notify');
    time.advance(40_000);
    limiter('notify');
    limiter('notify');
    limiter('notify');
    time.advance(30_000);

    // The first two have aged out; three are left, so two more fit.
    expect(limiter('notify')).toBe('admit');
    expect(limiter('notify')).toBe('admit');
    expect(limiter('notify')).toBe('first_over');
  });
});
