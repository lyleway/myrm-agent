import { describe, expect, it } from 'vitest';

import type { ExternalAgentConfig, ExternalAgentPermissionMode } from '@/services/config';
import {
  DEFAULT_AGENT,
  backendKeyFromCommand,
  cleanAgent,
  effectiveAuthMode,
  envRowProblems,
  newEnvRow,
  normalizePermissionMode,
  permissionHintKey,
  toDraft,
} from '../externalAgentModel';

const stored = (overrides: Partial<ExternalAgentConfig> = {}): ExternalAgentConfig => ({
  ...DEFAULT_AGENT,
  name: 'claude-code',
  command: 'claude',
  ...overrides,
});

describe('toDraft', () => {
  it('turns the stored environment into one editable line per variable', () => {
    const draft = toDraft(stored({ env: { HTTPS_PROXY: 'http://proxy:8080', ANTHROPIC_API_KEY: 'sk-1' } }));

    expect(draft.envRows.map(({ name, value }) => [name, value])).toEqual([
      ['HTTPS_PROXY', 'http://proxy:8080'],
      ['ANTHROPIC_API_KEY', 'sk-1'],
    ]);
    expect(new Set(draft.envRows.map((row) => row.id)).size).toBe(2);
    expect(draft).not.toHaveProperty('env');
  });

  it('has no lines for an agent without an environment', () => {
    expect(toDraft(stored()).envRows).toEqual([]);
  });
});

describe('cleanAgent', () => {
  it('stores the environment as a name to value map and keeps no editing state', () => {
    const draft = {
      ...toDraft(stored()),
      envRows: [newEnvRow(' ANTHROPIC_API_KEY ', 'sk-1'), newEnvRow('HTTPS_PROXY', 'http://proxy:8080')],
    };

    const cleaned = cleanAgent(draft);

    expect(cleaned.env).toEqual({ ANTHROPIC_API_KEY: 'sk-1', HTTPS_PROXY: 'http://proxy:8080' });
    expect(cleaned).not.toHaveProperty('envRows');
  });

  it('leaves blank lines out and stores no environment when nothing is left', () => {
    const cleaned = cleanAgent({ ...toDraft(stored({ env: { A: '1' } })), envRows: [newEnvRow(), newEnvRow('', '')] });

    expect(cleaned).not.toHaveProperty('env');
  });

  it('keeps a variable that is deliberately empty', () => {
    const cleaned = cleanAgent({ ...toDraft(stored()), envRows: [newEnvRow('NO_PROXY', '')] });

    expect(cleaned.env).toEqual({ NO_PROXY: '' });
  });

  it('gives back an unchanged agent as it was stored', () => {
    const agent = stored({ args: ['-p'], env: { HTTPS_PROXY: 'http://proxy:8080' }, description: 'd' });

    expect(cleanAgent(toDraft(agent))).toEqual(agent);
  });

  it('keeps the settings of an agent that this form does not edit', () => {
    const agent = { ...stored(), timeout: 600, maxResponseChars: 20000 } as ExternalAgentConfig;

    expect(cleanAgent(toDraft(agent))).toMatchObject({ timeout: 600, maxResponseChars: 20000 });
  });

  it('trims the text, drops blank arguments and replaces a mode of an older vocabulary', () => {
    const cleaned = cleanAgent({
      ...toDraft(stored({ name: '  claude-code ', command: ' claude ', args: ['-p', '  ', ''] })),
      permissionMode: 'ask' as ExternalAgentPermissionMode,
    });

    expect(cleaned).toMatchObject({ name: 'claude-code', command: 'claude', args: ['-p'], permissionMode: 'safe' });
  });

  it('keeps the chosen auth mode where subscription login is allowed', () => {
    expect(cleanAgent(toDraft(stored({ authMode: 'subscription' })), true).authMode).toBe('subscription');
    expect(cleanAgent(toDraft(stored({ authMode: 'api_key' })), true).authMode).toBe('api_key');
    expect(cleanAgent(toDraft(stored({ authMode: undefined }))).authMode).toBeUndefined();
  });

  it('stores an API key as the auth mode where subscription login is not allowed', () => {
    expect(cleanAgent(toDraft(stored({ authMode: 'subscription' })), false).authMode).toBe('api_key');
    expect(cleanAgent(toDraft(stored({ authMode: undefined })), false).authMode).toBe('api_key');
  });
});

describe('effectiveAuthMode', () => {
  it('defaults to subscription where login is allowed', () => {
    expect(effectiveAuthMode(undefined, true)).toBe('subscription');
    expect(effectiveAuthMode('api_key', true)).toBe('api_key');
  });

  it('is always the API key where login is not allowed', () => {
    expect(effectiveAuthMode(undefined, false)).toBe('api_key');
    expect(effectiveAuthMode('subscription', false)).toBe('api_key');
  });
});

describe('envRowProblems', () => {
  it.each(['A', '_hidden', 'http_proxy', 'ANTHROPIC_API_KEY', 'KEY2'])('accepts the name %s', (name) => {
    expect(envRowProblems([newEnvRow(name, 'v')]).size).toBe(0);
  });

  it.each(['1KEY', 'A-B', 'A B', 'A=B', 'KÉY', 'a.b'])('rejects the name %s as invalid', (name) => {
    const row = newEnvRow(name, 'v');

    expect(envRowProblems([row]).get(row.id)).toBe('invalid');
  });

  it('names a value that has no name', () => {
    const noName = newEnvRow('', 'sk-1');
    const spaces = newEnvRow('   ', 'sk-2');

    const problems = envRowProblems([noName, spaces]);

    expect(problems.get(noName.id)).toBe('missing');
    expect(problems.get(spaces.id)).toBe('missing');
  });

  it('does not complain about a blank line', () => {
    expect(envRowProblems([newEnvRow(), newEnvRow('  ', '')]).size).toBe(0);
  });

  it('points at the later line of a repeated name, even when padded', () => {
    const first = newEnvRow('KEY', '1');
    const second = newEnvRow(' KEY ', '2');

    const problems = envRowProblems([first, second]);

    expect(problems.has(first.id)).toBe(false);
    expect(problems.get(second.id)).toBe('duplicate');
  });
});

describe('permission and backend rules', () => {
  it('keeps a mode on offer and falls back to the safe one for anything else', () => {
    expect(normalizePermissionMode('allow_all')).toBe('allow_all');
    expect(normalizePermissionMode('safe')).toBe('safe');
    expect(normalizePermissionMode('ask')).toBe('safe');
    expect(normalizePermissionMode('bypass')).toBe('safe');
    expect(normalizePermissionMode('')).toBe('safe');
  });

  it.each([
    ['acp', 'anything', 'permissionHintAsk'],
    ['cli', 'claude', 'permissionHintAsk'],
    ['cli', '/opt/bin/claude', 'permissionHintAsk'],
    ['cli', 'codex', 'permissionHintReadOnly'],
    ['cli', 'gemini', 'permissionHintReadOnly'],
    ['cli', 'qwen', 'permissionHintReadOnly'],
    ['cli', 'aider', 'permissionHintUnrestricted'],
    ['cli', '', 'permissionHintUnrestricted'],
  ] as const)('explains what the safe mode means for a %s agent running %s', (type, command, hint) => {
    expect(permissionHintKey(type, command)).toBe(hint);
  });

  it.each([
    ['claude', 'claude'],
    ['/usr/local/bin/codex', 'codex'],
    ['C:\\tools\\gemini.cmd', 'gemini'],
    ['my-qwen-wrapper', 'qwen'],
    ['aider', null],
  ])('maps the command %s to the login backend %s', (command, backend) => {
    expect(backendKeyFromCommand(command)).toBe(backend);
  });
});
