/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../externalScripts::listExternalScripts
 *
 * [OUTPUT]
 * - Tests: other sites' scripts are listed by host; a script is locked by an integrity hash or an exact version only.
 *
 * [POS]
 * Feeds the notice that a permission covers one version of the page but not what a remote script may become.
 */

import { describe, expect, it } from 'vitest';
import { listExternalScripts } from '../externalScripts';

const page = (...scripts: string[]) => `<html><head>${scripts.join('')}</head><body><h1>app</h1></body></html>`;
const script = (src: string, attrs = '') => `<script src="${src}" ${attrs}></script>`;

describe('listExternalScripts', () => {
  it('lists nothing for a page that loads nothing from other sites', () => {
    expect(listExternalScripts(page('<script>window.x = 1</script>'))).toEqual([]);
    expect(listExternalScripts(page(script('/local.js'), script('app.js')))).toEqual([]);
  });

  it('ignores addresses that are not web addresses', () => {
    expect(listExternalScripts(page(script('data:text/javascript,1'), script('javascript:alert(1)')))).toEqual([]);
    expect(listExternalScripts(page(script('')))).toEqual([]);
  });

  it('treats an exact version in the address as locked', () => {
    expect(
      listExternalScripts(
        page(
          script('https://cdn.jsdelivr.net/npm/lodash@4.17.21/lodash.min.js'),
          script('https://cdnjs.cloudflare.com/ajax/libs/jquery/3.6.0/jquery.min.js'),
        ),
      ),
    ).toEqual([
      { host: 'cdn.jsdelivr.net', mayChange: false },
      { host: 'cdnjs.cloudflare.com', mayChange: false },
    ]);
  });

  it('treats a floating address as one that may change', () => {
    for (const src of [
      'https://unpkg.com/lodash',
      'https://unpkg.com/lodash@latest/lodash.js',
      'https://unpkg.com/vue@3/dist/vue.js',
      'https://unpkg.com/vue@3.4/dist/vue.js',
    ]) {
      expect(listExternalScripts(page(script(src)))).toEqual([{ host: 'unpkg.com', mayChange: true }]);
    }
  });

  it('treats an integrity hash as locking the content whatever the address says', () => {
    const hash = 'sha384-oqVuAfXRKap7fdgcCY5uykM6+R9GqQ8K/uxy9rx7HNQlGYl1kPzQho1wx4JwY8wC';

    expect(listExternalScripts(page(script('https://unpkg.com/lodash', `integrity="${hash}"`)))).toEqual([
      { host: 'unpkg.com', mayChange: false },
    ]);
    expect(listExternalScripts(page(script('https://unpkg.com/lodash', 'integrity="md5-nope"')))).toEqual([
      { host: 'unpkg.com', mayChange: true },
    ]);
  });

  it('resolves a protocol-relative address against https', () => {
    expect(listExternalScripts(page(script('//CDN.Example.com/lib@1.2.3/x.js')))).toEqual([
      { host: 'cdn.example.com', mayChange: false },
    ]);
  });

  it('shows a host once, floating if any of its scripts is', () => {
    expect(
      listExternalScripts(
        page(script('https://cdn.example.com/a@1.0.0/a.js'), script('https://cdn.example.com/b/b.js')),
      ),
    ).toEqual([{ host: 'cdn.example.com', mayChange: true }]);
  });

  it('orders hosts by name and lists at most twenty', () => {
    const many = Array.from({ length: 25 }, (_, i) => script(`https://h${String(i).padStart(2, '0')}.example.com/x.js`));

    const listed = listExternalScripts(page(script('https://b.example.com/x.js'), script('https://a.example.com/x.js'), ...many));

    expect(listed).toHaveLength(20);
    expect(listed[0].host).toBe('a.example.com');
    expect(listed[1].host).toBe('b.example.com');
  });
});
