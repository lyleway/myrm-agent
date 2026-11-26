import { describe, expect, it } from 'vitest';

import { resolveExternalCliReadiness, hasAutoDetectedExternalCliBackend } from '@/services/external-agents';

describe('external CLI readiness', () => {
  it('distinguishes not-configured from configured-but-unavailable', () => {
    const missing = { backend: 'claude', installed: false, authenticated: false } as never;
    const present = { backend: 'claude', installed: true, authenticated: false } as never;

    // Nothing configured: the user still has to add a backend.
    expect(resolveExternalCliReadiness([], [], true)).toBe('not_configured');
    expect(resolveExternalCliReadiness([{ enabled: false, command: 'claude' }], [], true)).toBe('not_configured');
    expect(resolveExternalCliReadiness([{ enabled: true, command: '   ' }], [], false)).toBe('not_configured');

    // Configured but the binary is missing: a different problem, needing a different fix.
    expect(resolveExternalCliReadiness([{ enabled: true, command: 'claude' }], [missing], true)).toBe('unavailable');

    // Ready once the server confirms the binary, or when the server cannot judge the command.
    expect(resolveExternalCliReadiness([{ enabled: true, command: 'claude' }], [present], true)).toBe('ready');
    expect(resolveExternalCliReadiness([{ enabled: true, command: 'my-wrapper' }], [missing], true)).toBe('ready');
    expect(resolveExternalCliReadiness([{ enabled: true, command: '/opt/homebrew/bin/claude' }], [missing], true)).toBe(
      'ready',
    );

    // A derived or wrapped binary is a different program the server cannot vouch for, so
    // it must not inherit another backend's negative detection result.
    expect(resolveExternalCliReadiness([{ enabled: true, command: 'claude-bedrock' }], [missing], true)).toBe('ready');
    expect(resolveExternalCliReadiness([{ enabled: true, command: 'my-claude-wrapper' }], [missing], true)).toBe(
      'ready',
    );

    // A locally auto-detected CLI counts as ready even with nothing configured in-app.
    expect(resolveExternalCliReadiness([], [present], true)).toBe('ready');
    expect(resolveExternalCliReadiness([], [present], false)).toBe('not_configured');
    // A whitespace-only command counts as nothing configured. With no local auto-detected
    // CLI to fall back on, that must read as not_configured rather than a failed backend.
    expect(resolveExternalCliReadiness([{ enabled: true, command: '   ' }], [missing], false)).toBe('not_configured');
    // A path with no basename cannot be matched to a backend row; it stays trusted.
    expect(resolveExternalCliReadiness([{ enabled: true, command: '/' }], [missing], false)).toBe('ready');
    expect(
      hasAutoDetectedExternalCliBackend([{ backend: 'claude', installed: true, authenticated: false } as never]),
    ).toBe(true);
  });
});
