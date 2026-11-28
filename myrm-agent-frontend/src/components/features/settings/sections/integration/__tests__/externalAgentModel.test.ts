import { describe, expect, it } from 'vitest';

import type { ExternalAgentConfig, ExternalAgentPermissionMode } from '@/services/config';
import {
  DEFAULT_AGENT,
  accountBackend,
  availablePresets,
  backendKeyFromCommand,
  cleanAgent,
  contractTransport,
  detachAccount,
  effectiveAuthMode,
  envRowProblems,
  isValidAccountName,
  knownBackendOfCommand,
  memoryNeedsTrustedFolder,
  newEnvRow,
  normalizePermissionMode,
  permissionHintKey,
  toDraft,
} from '../externalAgentModel';
import { contract, knownContracts, newContract } from './knownAgents';

const KNOWN = knownContracts();

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

    const cleaned = cleanAgent(draft, KNOWN);

    expect(cleaned.env).toEqual({ ANTHROPIC_API_KEY: 'sk-1', HTTPS_PROXY: 'http://proxy:8080' });
    expect(cleaned).not.toHaveProperty('envRows');
  });

  it('leaves blank lines out and stores no environment when nothing is left', () => {
    const cleaned = cleanAgent(
      { ...toDraft(stored({ env: { A: '1' } })), envRows: [newEnvRow(), newEnvRow('', '')] },
      KNOWN,
    );

    expect(cleaned).not.toHaveProperty('env');
  });

  it('keeps a variable that is deliberately empty', () => {
    const cleaned = cleanAgent({ ...toDraft(stored()), envRows: [newEnvRow('NO_PROXY', '')] }, KNOWN);

    expect(cleaned.env).toEqual({ NO_PROXY: '' });
  });

  it('gives back an unchanged agent as it was stored', () => {
    const agent = stored({ args: ['-p'], env: { HTTPS_PROXY: 'http://proxy:8080' }, description: 'd' });

    expect(cleanAgent(toDraft(agent), KNOWN)).toEqual(agent);
  });

  it('keeps the settings of an agent that this form does not edit', () => {
    const agent = { ...stored(), timeout: 600, maxResponseChars: 20000 } as ExternalAgentConfig;

    expect(cleanAgent(toDraft(agent), KNOWN)).toMatchObject({ timeout: 600, maxResponseChars: 20000 });
  });

  it('trims the text, drops blank arguments and replaces a mode of an older vocabulary', () => {
    const cleaned = cleanAgent(
      {
        ...toDraft(stored({ name: '  claude-code ', command: ' claude ', args: ['-p', '  ', ''] })),
        permissionMode: 'ask' as ExternalAgentPermissionMode,
      },
      KNOWN,
    );

    expect(cleaned).toMatchObject({ name: 'claude-code', command: 'claude', args: ['-p'], permissionMode: 'safe' });
  });

  it('keeps the chosen auth mode where subscription login is allowed', () => {
    expect(cleanAgent(toDraft(stored({ authMode: 'subscription' })), KNOWN, true).authMode).toBe('subscription');
    expect(cleanAgent(toDraft(stored({ authMode: 'api_key' })), KNOWN, true).authMode).toBe('api_key');
    expect(cleanAgent(toDraft(stored({ authMode: undefined })), KNOWN).authMode).toBeUndefined();
  });

  it('stores an API key as the auth mode where subscription login is not allowed', () => {
    expect(cleanAgent(toDraft(stored({ authMode: 'subscription' })), KNOWN, false).authMode).toBe('api_key');
    expect(cleanAgent(toDraft(stored({ authMode: undefined })), KNOWN, false).authMode).toBe('api_key');
  });

  it('stores sharing memory only as the explicit true of a consent', () => {
    expect(cleanAgent(toDraft(stored({ shareMemory: true })), KNOWN).shareMemory).toBe(true);
    expect(cleanAgent(toDraft(stored({ shareMemory: false })), KNOWN)).not.toHaveProperty('shareMemory');
    expect(cleanAgent(toDraft(stored()), KNOWN)).not.toHaveProperty('shareMemory');
  });

  it('does not take a stored value that is not a boolean true for a consent', () => {
    for (const value of ['true', 'yes', 1, {}]) {
      const agent = { ...stored(), shareMemory: value } as unknown as ExternalAgentConfig;

      expect(cleanAgent(toDraft(agent), KNOWN)).not.toHaveProperty('shareMemory');
    }
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
    ['app_server', 'anything', 'permissionHintAsk'],
    ['cli', 'claude', 'permissionHintAsk'],
    ['cli', '/opt/bin/claude', 'permissionHintAsk'],
    ['app_server', 'codex', 'permissionHintAsk'],
    ['acp', 'gemini', 'permissionHintAsk'],
    ['acp', 'qwen', 'permissionHintAsk'],
    ['cli', 'aider', 'permissionHintUnrestricted'],
    ['cli', 'codex-wrapper', 'permissionHintUnrestricted'],
    ['cli', '', 'permissionHintUnrestricted'],
  ] as const)('explains what the safe mode means for a %s agent running %s', (type, command, hint) => {
    expect(permissionHintKey(type, command, KNOWN)).toBe(hint);
  });

  it.each([
    ['claude', 'claude'],
    ['/usr/local/bin/codex', 'codex'],
    ['C:\\tools\\gemini.cmd', 'gemini'],
    ['my-qwen-wrapper', 'qwen'],
    ['aider', null],
  ])('maps the command %s to the login backend %s', (command, backend) => {
    expect(backendKeyFromCommand(command, KNOWN)).toBe(backend);
  });
});

describe('the presets the known agents give', () => {
  it('offers none before the server has answered', () => {
    expect(availablePresets([])).toEqual([]);
  });

  it('offers one preset per known agent, labelled and started as the agent says', () => {
    const presets = availablePresets(KNOWN);

    expect(presets.map((preset) => [preset.key, preset.label])).toEqual([
      ['claude', 'Claude Code'],
      ['codex', 'Codex CLI'],
      ['gemini', 'Gemini CLI'],
      ['qwen', 'Qwen Code'],
    ]);
    for (const [index, entry] of KNOWN.entries()) {
      expect(presets[index].config).toEqual({
        ...DEFAULT_AGENT,
        name: entry.defaultName,
        type: entry.transport,
        command: entry.backend,
        description: entry.description,
      });
    }
  });

  it('leaves out the agents the deployment does not run', () => {
    const hosted = [
      contract('claude', { availableInDeployment: false }),
      contract('codex', { availableInDeployment: false }),
      contract('gemini'),
      contract('qwen'),
    ];

    expect(availablePresets(hosted).map((preset) => preset.key)).toEqual(['gemini', 'qwen']);
  });

  it('gives an agent the server starts to report a preset without a change here', () => {
    const [preset] = availablePresets([newContract('aider', 'cli')]);

    expect(preset).toMatchObject({
      key: 'aider',
      label: 'aider agent',
      config: { name: 'aider-cli', command: 'aider', type: 'cli', args: [], description: 'The aider agent' },
    });
  });

  it('starts every agent with the arguments of its own launch: none', () => {
    for (const preset of availablePresets(KNOWN)) {
      expect(preset.config.args, preset.key).toEqual([]);
    }
  });
});

describe('the transport of a known agent', () => {
  it.each([
    ['gemini', 'gemini'],
    ['/usr/local/bin/gemini', 'gemini'],
    ['C:\\tools\\Gemini.CMD', 'gemini'],
    ['qwen', 'qwen'],
    ['claude', 'claude'],
    // A wrapper or adapter that carries an agent's name is a command of its own.
    ['gemini-wrapper', null],
    ['my-qwen-wrapper', null],
    ['claude-code-acp', null],
    ['aider', null],
    ['', null],
  ])('knows the command %s as the agent %s', (command, backend) => {
    expect(knownBackendOfCommand(command, KNOWN)).toBe(backend);
  });

  it('knows no agent before the server has answered, and the one a new entry names after it', () => {
    expect(knownBackendOfCommand('claude', [])).toBeNull();
    expect(knownBackendOfCommand('aider', [...KNOWN, newContract('aider', 'cli')])).toBe('aider');
  });

  it.each([
    ['gemini', 'acp'],
    ['/usr/local/bin/qwen', 'acp'],
    ['claude', 'cli'],
    ['codex.cmd', 'app_server'],
    ['gemini-wrapper', null],
    ['claude-code-acp', null],
    ['aider', null],
  ])('fixes the transport of the command %s to %s', (command, transport) => {
    expect(contractTransport(command, KNOWN)).toBe(transport);
  });

  it('fixes nothing before the server has answered', () => {
    expect(contractTransport('gemini', [])).toBeNull();
  });

  it('stores the fixed transport in place of a stale one', () => {
    const agent = stored({ name: 'gemini-cli', command: 'gemini', type: 'cli' });

    expect(cleanAgent(toDraft(agent), KNOWN).type).toBe('acp');
    expect(cleanAgent(toDraft(agent), []).type).toBe('cli');
  });

  it('starts every preset in the transport its own contract fixes', () => {
    for (const preset of availablePresets(KNOWN)) {
      expect(contractTransport(preset.config.command, KNOWN), preset.key).toBe(preset.config.type);
    }
  });
});

describe('accounts', () => {
  it.each(['work', 'a', 'a-b_c', '0x', 'x'.repeat(32)])('accepts the name %s', (name) => {
    expect(isValidAccountName(name)).toBe(true);
  });

  it.each(['', 'Work', '-lead', '_lead', 'a b', 'a.b', 'a/b', '..', 'x'.repeat(33), 'é', 'work\n'])(
    'refuses the name %j',
    (name) => {
      expect(isValidAccountName(name)).toBe(false);
    },
  );

  it.each([
    ['claude', 'subscription', true, 'claude'],
    ['/opt/bin/codex', undefined, true, 'codex'],
    ['C:\\tools\\gemini.cmd', 'subscription', true, 'gemini'],
    ['claude', 'api_key', true, null],
    ['claude', 'subscription', false, null],
    ['claude-code', 'subscription', true, null],
    ['aider', 'subscription', true, null],
    ['', 'subscription', true, null],
  ] as const)(
    'lets the command %s on %s login (allowed: %s) choose an account of %s',
    (command, mode, allowed, backend) => {
      expect(accountBackend(command, mode, allowed, KNOWN)).toBe(backend);
    },
  );

  it('lets no command choose an account before the server has answered', () => {
    expect(accountBackend('claude', 'subscription', true, [])).toBeNull();
  });

  describe('when stored', () => {
    const work = (overrides: Partial<ExternalAgentConfig> = {}) =>
      stored({ authMode: 'subscription', account: 'work', ...overrides });

    it('keeps the account of a known agent on its subscription login', () => {
      expect(cleanAgent(toDraft(work()), KNOWN, true).account).toBe('work');
    });

    it.each([
      ['an API key', work({ authMode: 'api_key' }), true],
      ['a deployment without subscription login', work(), false],
      ['a command the app does not know', work({ command: 'aider' }), true],
      ['a blank account', work({ account: '' }), true],
    ])('drops the account of an agent on %s', (_label, agent, allowed) => {
      expect(cleanAgent(toDraft(agent), KNOWN, allowed)).not.toHaveProperty('account');
    });

    it('stores an agent without an account as it was', () => {
      expect(cleanAgent(toDraft(stored({ authMode: 'subscription' })), KNOWN, true)).not.toHaveProperty('account');
    });
  });

  describe('when one is removed', () => {
    const agent = (name: string, command: string, account?: string): ExternalAgentConfig =>
      stored({ name, command, ...(account ? { account } : {}) });

    it('detaches it from the agents of that backend that ran as it, and from no other', () => {
      const agents = [
        agent('a', 'claude', 'work'),
        agent('b', 'claude', 'home'),
        agent('c', '/usr/bin/claude', 'work'),
        agent('d', 'codex', 'work'),
        agent('e', 'claude'),
      ];

      const left = detachAccount(agents, 'claude', 'work', KNOWN);

      expect(left.map(({ name, account }) => [name, account])).toEqual([
        ['a', undefined],
        ['b', 'home'],
        ['c', undefined],
        ['d', 'work'],
        ['e', undefined],
      ]);
      expect(left[0]).not.toHaveProperty('account');
      expect(agents[0].account).toBe('work');
    });

    it('hands back the same list when no agent ran as it', () => {
      const agents = [agent('a', 'claude', 'home'), agent('b', 'codex', 'work')];

      expect(detachAccount(agents, 'claude', 'work', KNOWN)).toBe(agents);
    });
  });
});

describe('memoryNeedsTrustedFolder', () => {
  it('is true for the agent whose entry says so wherever it lives and whatever its extension', () => {
    expect(memoryNeedsTrustedFolder('gemini', KNOWN)).toBe(true);
    expect(memoryNeedsTrustedFolder('/usr/local/bin/gemini', KNOWN)).toBe(true);
    expect(memoryNeedsTrustedFolder('C:\\tools\\gemini.cmd', KNOWN)).toBe(true);
  });

  it('is false for the other agents, for a command that only carries a name, and before the server has answered', () => {
    for (const command of ['claude', 'codex', 'qwen', 'gemini-wrapper', '']) {
      expect(memoryNeedsTrustedFolder(command, KNOWN)).toBe(false);
    }
    expect(memoryNeedsTrustedFolder('gemini', [])).toBe(false);
  });

  it('follows the entry, not the name of the agent', () => {
    const entries = [
      contract('claude', { mcpNeedsTrustedFolder: true }),
      contract('gemini', { mcpNeedsTrustedFolder: false }),
    ];

    expect(memoryNeedsTrustedFolder('claude', entries)).toBe(true);
    expect(memoryNeedsTrustedFolder('gemini', entries)).toBe(false);
  });
});
