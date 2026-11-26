import { describe, expect, it } from 'vitest';
import {
  EMPTY_DECLARATION,
  declaredScope,
  isExactPublicHost,
  isSubset,
  parseDeclaration,
  resolveStatuses,
  type Declaration,
  type GrantRecord,
} from '../declaration';

const page = (block: string) =>
  `<!DOCTYPE html><html><head><title>x</title></head><body>${block}<h1>Hi</h1></body></html>`;
const declare = (json: string) => page(`<script type="application/json" id="myrm-app">${json}</script>`);

describe('parseDeclaration', () => {
  it('reads both capabilities', () => {
    expect(parseDeclaration(declare('{"notify": true, "fetch": ["api.weather.com", "b.example.org"]}'))).toEqual({
      notify: true,
      fetchHosts: ['api.weather.com', 'b.example.org'],
    });
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
    expect(parseDeclaration(html)).toEqual({ notify: true, fetchHosts: [] });
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
    const declaration: Declaration = { notify: true, fetchHosts: ['a.example.com'] };
    expect(declaredScope('notify', declaration)).toEqual([]);
    expect(declaredScope('fetch', declaration)).toEqual(['a.example.com']);
    expect(declaredScope('notify', EMPTY_DECLARATION)).toBeNull();
    expect(declaredScope('fetch', EMPTY_DECLARATION)).toBeNull();
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
  const both: Declaration = { notify: true, fetchHosts: ['a.example.com'] };

  it('marks what the page does not declare as undeclared, whatever was granted before', () => {
    expect(resolveStatuses(EMPTY_DECLARATION, [grant('notify', 'allow', [])])).toEqual({
      notify: 'undeclared',
      fetch: 'undeclared',
    });
  });

  it('asks for every declared capability that has no decision yet', () => {
    expect(resolveStatuses(both, [])).toEqual({ notify: 'ask', fetch: 'ask' });
  });

  it('applies a stored allow or deny while the declaration stays within the decided scope', () => {
    expect(resolveStatuses(both, [grant('notify', 'allow', []), grant('fetch', 'deny', ['a.example.com'])])).toEqual({
      notify: 'allowed',
      fetch: 'denied',
    });
  });

  it('keeps an allow when the new version narrows the hosts', () => {
    const narrower: Declaration = { notify: false, fetchHosts: ['a.example.com'] };
    expect(resolveStatuses(narrower, [grant('fetch', 'allow', ['a.example.com', 'b.example.com'])]).fetch).toBe(
      'allowed',
    );
  });

  it('asks again when the declaration reaches a host the user never decided on', () => {
    const wider: Declaration = { notify: false, fetchHosts: ['a.example.com', 'new.example.com'] };
    expect(resolveStatuses(wider, [grant('fetch', 'allow', ['a.example.com'])]).fetch).toBe('ask');
    expect(resolveStatuses(wider, [grant('fetch', 'deny', ['a.example.com'])]).fetch).toBe('ask');
  });

  it('does not let a notify decision stand in for fetch', () => {
    expect(resolveStatuses(both, [grant('notify', 'allow', [])]).fetch).toBe('ask');
  });
});
