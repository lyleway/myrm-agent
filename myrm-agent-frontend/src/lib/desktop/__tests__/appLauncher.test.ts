/**
 * [INPUT]
 * - ../appLauncher::hasAppLauncher / createAppLauncher / removeAppLauncher
 *
 * [OUTPUT]
 * - Tests: each call reaches its shell command with the app's id; a refusal is a value, not an exception.
 *
 * [POS]
 * The menu shows a failure instead of breaking, so these calls must never throw.
 */

import { beforeEach, describe, expect, it, vi } from 'vitest';

const invoke = vi.hoisted(() => vi.fn());
vi.mock('@tauri-apps/api/core', () => ({ invoke }));

import { createAppLauncher, hasAppLauncher, removeAppLauncher } from '../appLauncher';

describe('app launcher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(console, 'error').mockImplementation(() => undefined);
  });

  it('invokes each command with the app it concerns', async () => {
    invoke.mockResolvedValue(undefined);

    await expect(createAppLauncher('art-1', 'Weather')).resolves.toBe(true);
    await expect(removeAppLauncher('art-1')).resolves.toBe(true);

    expect(invoke.mock.calls).toEqual([
      ['create_app_launcher', { artifactId: 'art-1', name: 'Weather' }],
      ['remove_app_launcher', { artifactId: 'art-1' }],
    ]);
  });

  it('reads whether the app has a launcher', async () => {
    invoke.mockResolvedValueOnce(true).mockResolvedValueOnce(false);

    await expect(hasAppLauncher('art-1')).resolves.toBe(true);
    await expect(hasAppLauncher('art-1')).resolves.toBe(false);
    expect(invoke).toHaveBeenCalledWith('has_app_launcher', { artifactId: 'art-1' });
  });

  it('reports a refused call as failure instead of throwing', async () => {
    invoke.mockRejectedValue('unsupported');

    await expect(createAppLauncher('art-1', 'Weather')).resolves.toBe(false);
    await expect(removeAppLauncher('art-1')).resolves.toBe(false);
    await expect(hasAppLauncher('art-1')).resolves.toBe(false);
  });
});
