import { describe, expect, it } from 'vitest';

import {
  hasAutoDetectedExternalCliBackend,
  isBlockedInDeployment,
  resolveExternalCliReadiness,
} from '@/services/external-agents';

describe('external CLI readiness', () => {
  it('distinguishes not-configured from configured-but-unavailable', () => {
    const missing = { backend: 'claude', installed: false, authenticated: false, availableInDeployment: true } as never;
    const present = { backend: 'claude', installed: true, authenticated: false, availableInDeployment: true } as never;

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
      hasAutoDetectedExternalCliBackend([
        { backend: 'claude', installed: true, authenticated: false, availableInDeployment: true } as never,
      ]),
    ).toBe(true);
  });
});

describe('agents the deployment does not run', () => {
  const row = (backend: string, availableInDeployment: boolean, installed = availableInDeployment) =>
    ({ backend, installed, authenticated: false, availableInDeployment }) as never;
  const hosted = [row('claude', false), row('codex', false), row('gemini', true), row('qwen', true)];

  it.each(['claude', 'codex', 'CLAUDE', '/usr/local/bin/claude', 'C:\\tools\\codex.cmd', 'claude-code', '  codex  '])(
    'names a blocked agent in %s',
    (command) => {
      expect(isBlockedInDeployment(command, hosted)).toBe(true);
    },
  );

  it.each(['gemini', 'qwen', '/opt/bin/my-agent', '', '   ', '/'])('does not block %j', (command) => {
    expect(isBlockedInDeployment(command, hosted)).toBe(false);
  });

  it('blocks nothing until the server has reported, and nothing where every agent is available', () => {
    expect(isBlockedInDeployment('claude', [])).toBe(false);
    expect(isBlockedInDeployment('claude', [row('claude', true), row('codex', true)])).toBe(false);
  });

  it('reads blocked when every configured agent is blocked, however the command is spelled', () => {
    const agents = [
      { enabled: true, command: 'claude' },
      { enabled: true, command: '/usr/bin/codex' },
    ];

    expect(resolveExternalCliReadiness(agents, hosted, false)).toBe('blocked');
    expect(resolveExternalCliReadiness([{ enabled: true, command: 'claude-code' }], hosted, false)).toBe('blocked');
  });

  it('judges only the agents that are left once the blocked ones are set aside', () => {
    const blockedAndMine = [
      { enabled: true, command: 'claude' },
      { enabled: true, command: 'my-wrapper' },
    ];
    const blockedAndMissing = [
      { enabled: true, command: 'claude' },
      { enabled: true, command: 'gemini' },
    ];
    const noGemini = [row('claude', false), row('codex', false), row('gemini', true, false)];

    expect(resolveExternalCliReadiness(blockedAndMine, hosted, false)).toBe('ready');
    expect(resolveExternalCliReadiness(blockedAndMissing, hosted, false)).toBe('ready');
    expect(resolveExternalCliReadiness(blockedAndMissing, noGemini, false)).toBe('unavailable');
  });

  it('does not read a disabled blocked agent as blocked', () => {
    expect(resolveExternalCliReadiness([{ enabled: false, command: 'claude' }], hosted, false)).toBe('not_configured');
  });
});
