import { describe, expect, it } from 'vitest';
import { BRIDGE_CAPABILITIES } from '../protocol';
import {
  EMPTY_DECLARATION,
  declaredScope,
  isExactPublicHost,
  isSubset,
  offeredCapabilities,
  parseDeclaration,
  resolveStatuses,
  type Declaration,
  type GrantRecord,
} from '../declaration';

const page = (block: string) =>
  `<!DOCTYPE html><html><head><title>x</title></head><body>${block}<h1>Hi</h1></body></html>`;
const declare = (json: string) => page(`<script type="application/json" id="myrm-app">${json}</script>`);

describe('parseDeclaration', () => {
  it('reads the capabilities of notification and network', () => {
    expect(parseDeclaration(declare('{"notify": true, "fetch": ["api.weather.com", "b.example.org"]}'))).toEqual({
      ...EMPTY_DECLARATION,
      notify: true,
      fetchHosts: ['api.weather.com', 'b.example.org'],
    });
  });

  it('reads the desktop capabilities, spelling the key combinations canonically', () => {
    expect(
      parseDeclaration(
        declare('{"tray": true, "float": true, "shortcut": ["shift+commandorcontrol+k", "Alt+F1", "alt+f1"]}'),
      ),
    ).toEqual({
      ...EMPTY_DECLARATION,
      tray: true,
      float: true,
      shortcuts: ['Alt+F1', 'CommandOrControl+Shift+K'],
    });
  });

  it('only a literal true declares tray, float or ai', () => {
    const declaration = parseDeclaration(declare('{"tray": "yes", "float": 1, "ai": "true"}'));
    expect([declaration.tray, declaration.float, declaration.ai]).toEqual([false, false, false]);
    expect(parseDeclaration(declare('{"ai": true}'))).toEqual({ ...EMPTY_DECLARATION, ai: true });
  });

  it('drops the whole shortcut list when one combination is not allowed or there are more than four', () => {
    for (const list of [
      '["K"]',
      '["Shift+K"]',
      '["Alt+F1", "Super+K"]',
      '["Alt+1","Alt+2","Alt+3","Alt+4","Alt+5"]',
      '"Alt+F1"',
      '[]',
      '[1]',
    ]) {
      expect(parseDeclaration(declare(`{"shortcut": ${list}}`)).shortcuts, list).toEqual([]);
    }
  });

  it('canonicalises hosts: trimmed, lower-cased, de-duplicated, sorted', () => {
    expect(
      parseDeclaration(declare('{"fetch": [" B.Example.org", "a.example.org", "b.example.org"]}')).fetchHosts,
    ).toEqual(['a.example.org', 'b.example.org']);
  });

  it('declares nothing for a page without the block', () => {
    expect(parseDeclaration(page(''))).toEqual(EMPTY_DECLARATION);
  });

  it('ignores a block that is not JSON, not an object, or has the wrong element type or id', () => {
    expect(parseDeclaration(declare('{not json'))).toEqual(EMPTY_DECLARATION);
    expect(parseDeclaration(declare('[true]'))).toEqual(EMPTY_DECLARATION);
    expect(parseDeclaration(declare('null'))).toEqual(EMPTY_DECLARATION);
    expect(parseDeclaration(page('<script type="text/plain" id="myrm-app">{"notify":true}</script>'))).toEqual(
      EMPTY_DECLARATION,
    );
    expect(parseDeclaration(page('<script type="application/json" id="other">{"notify":true}</script>'))).toEqual(
      EMPTY_DECLARATION,
    );
  });

  it('only a literal true declares notify', () => {
    expect(parseDeclaration(declare('{"notify": "yes"}')).notify).toBe(false);
    expect(parseDeclaration(declare('{"notify": 1}')).notify).toBe(false);
  });

  it('drops the whole fetch list when any entry is not an exact public host name', () => {
    for (const hosts of [
      '["https://api.example.com"]',
      '["api.example.com/path"]',
      '["*.example.com"]',
      '["localhost"]',
      '["127.0.0.1"]',
      '["api.example.com:8443"]',
      '["api.example.com", "10.0.0.1"]',
      '"api.example.com"',
      '[]',
      '[1]',
    ]) {
      expect(parseDeclaration(declare(`{"fetch": ${hosts}}`)).fetchHosts, hosts).toEqual([]);
    }
  });

  it('refuses more than 20 hosts', () => {
    const hosts = Array.from({ length: 21 }, (_, index) => `h${index}.example.com`);
    expect(parseDeclaration(declare(JSON.stringify({ fetch: hosts }))).fetchHosts).toEqual([]);
    expect(parseDeclaration(declare(JSON.stringify({ fetch: hosts.slice(0, 20) }))).fetchHosts).toHaveLength(20);
  });

  it('takes the first block when a page carries several', () => {
    const html = page(
      '<script type="application/json" id="myrm-app">{"notify":true}</script><script type="application/json" id="myrm-app">{"fetch":["a.example.com"]}</script>',
    );
    expect(parseDeclaration(html)).toEqual({ ...EMPTY_DECLARATION, notify: true });
  });

  it('does not run scripts while reading', () => {
    const marker = '__declaration_probe__';
    parseDeclaration(page(`<script>window.${marker}=1</script>`));
    expect((window as unknown as Record<string, unknown>)[marker]).toBeUndefined();
  });
});

describe('isExactPublicHost', () => {
  it.each(['api.example.com', 'a-b.example.co.uk', 'x1.y2.example.io'])('accepts %s', (name) => {
    expect(isExactPublicHost(name)).toBe(true);
  });

  it.each([
    'localhost',
    'example',
    '1.2.3.4',
    'a.example.com:443',
    'Example.com',
    '-a.example.com',
    'a..example.com',
    '',
  ])('rejects %j', (name) => {
    expect(isExactPublicHost(name)).toBe(false);
  });
});

describe('declaredScope / isSubset', () => {
  it('maps a declaration to the scope of each capability, or null when undeclared', () => {
    const declaration: Declaration = {
      notify: true,
      fetchHosts: ['a.example.com'],
      ai: true,
      tray: true,
      float: true,
      shortcuts: ['Alt+F1'],
    };
    expect(declaredScope('notify', declaration)).toEqual([]);
    expect(declaredScope('fetch', declaration)).toEqual(['a.example.com']);
    expect(declaredScope('ai', declaration)).toEqual([]);
    expect(declaredScope('tray', declaration)).toEqual([]);
    expect(declaredScope('float', declaration)).toEqual([]);
    expect(declaredScope('shortcut', declaration)).toEqual(['Alt+F1']);
    for (const capability of BRIDGE_CAPABILITIES) {
      expect(declaredScope(capability, EMPTY_DECLARATION), capability).toBeNull();
    }
  });

  it('is a subset check', () => {
    expect(isSubset([], [])).toBe(true);
    expect(isSubset(['a.example.com'], ['a.example.com', 'b.example.com'])).toBe(true);
    expect(isSubset(['c.example.com'], ['a.example.com'])).toBe(false);
  });
});

describe('resolveStatuses', () => {
  const sha = 'a'.repeat(64);
  const grant = (
    capability: GrantRecord['capability'],
    decision: GrantRecord['decision'],
    scope: string[],
  ): GrantRecord => ({
    capability,
    decision,
    scope,
    version_sha256: sha,
  });
  const both: Declaration = { ...EMPTY_DECLARATION, notify: true, fetchHosts: ['a.example.com'] };
  const undeclared = {
    notify: 'undeclared',
    fetch: 'undeclared',
    ai: 'undeclared',
    tray: 'undeclared',
    shortcut: 'undeclared',
    float: 'undeclared',
  };

  it('marks what the page does not declare as undeclared, whatever was granted before', () => {
    expect(resolveStatuses(EMPTY_DECLARATION, [grant('notify', 'allow', [])])).toEqual(undeclared);
  });

  it('asks for every declared capability that has no decision yet', () => {
    expect(resolveStatuses(both, [])).toEqual({ ...undeclared, notify: 'ask', fetch: 'ask' });
  });

  it('applies a stored allow or deny while the declaration stays within the decided scope', () => {
    expect(resolveStatuses(both, [grant('notify', 'allow', []), grant('fetch', 'deny', ['a.example.com'])])).toEqual({
      ...undeclared,
      notify: 'allowed',
      fetch: 'denied',
    });
  });

  it('keeps an allow when the new version narrows the hosts', () => {
    const narrower: Declaration = { ...EMPTY_DECLARATION, fetchHosts: ['a.example.com'] };
    expect(resolveStatuses(narrower, [grant('fetch', 'allow', ['a.example.com', 'b.example.com'])]).fetch).toBe(
      'allowed',
    );
  });

  it('asks again when the declaration reaches a host the user never decided on', () => {
    const wider: Declaration = { ...EMPTY_DECLARATION, fetchHosts: ['a.example.com', 'new.example.com'] };
    expect(resolveStatuses(wider, [grant('fetch', 'allow', ['a.example.com'])]).fetch).toBe('ask');
    expect(resolveStatuses(wider, [grant('fetch', 'deny', ['a.example.com'])]).fetch).toBe('ask');
  });

  it('asks again when a new version declares a key combination the user never saw', () => {
    const declared: Declaration = { ...EMPTY_DECLARATION, shortcuts: ['Alt+F1', 'Alt+F2'] };

    expect(resolveStatuses(declared, [grant('shortcut', 'allow', ['Alt+F1'])]).shortcut).toBe('ask');
    expect(resolveStatuses(declared, [grant('shortcut', 'allow', ['Alt+F1', 'Alt+F2', 'Alt+F3'])]).shortcut).toBe(
      'allowed',
    );
  });

  it('keeps a decision about a scopeless capability while the page still declares it', () => {
    const tray: Declaration = { ...EMPTY_DECLARATION, tray: true };

    expect(resolveStatuses(tray, [grant('tray', 'allow', [])]).tray).toBe('allowed');
    expect(resolveStatuses(tray, [grant('tray', 'deny', [])]).tray).toBe('denied');
    expect(resolveStatuses(EMPTY_DECLARATION, [grant('tray', 'allow', [])]).tray).toBe('undeclared');
  });

  it('treats ai like any scopeless capability: asked once, then the decision stands', () => {
    const declared: Declaration = { ...EMPTY_DECLARATION, ai: true };

    expect(resolveStatuses(declared, []).ai).toBe('ask');
    expect(resolveStatuses(declared, [grant('ai', 'allow', [])]).ai).toBe('allowed');
    expect(resolveStatuses(declared, [grant('ai', 'deny', [])]).ai).toBe('denied');
    expect(resolveStatuses(EMPTY_DECLARATION, [grant('ai', 'allow', [])]).ai).toBe('undeclared');
  });

  it('offers ai in every window, unlike the desktop capabilities', () => {
    const statuses = resolveStatuses({ ...EMPTY_DECLARATION, ai: true, tray: true }, []);

    expect(offeredCapabilities(statuses, false)).toEqual(['ai']);
    expect(offeredCapabilities(statuses, true)).toEqual(['ai', 'tray']);
  });

  it('does not let a notify decision stand in for fetch', () => {
    expect(resolveStatuses(both, [grant('notify', 'allow', [])]).fetch).toBe('ask');
  });
});
