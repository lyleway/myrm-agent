/**
 * [INPUT]
 * - ../app-manifest::isAppManifestPath / appManifestHref / buildAppManifest
 *
 * [OUTPUT]
 * - Tests: the manifest keeps the install scoped to one app; only a well-formed id and a cleaned name get through; only
 *   the manifest file counts as an open app path.
 *
 * [POS]
 * The manifest is served without a session, so what it accepts and what it returns are the whole attack surface.
 */

import { describe, expect, it } from 'vitest';
import { appManifestHref, buildAppManifest, isAppManifestPath } from '../app-manifest';

describe('buildAppManifest', () => {
  it('scopes the install to the one app and opens it without the app shell', () => {
    const manifest = buildAppManifest('art-1', 'Weather');

    expect(manifest).toMatchObject({
      id: '/apps/art-1/',
      scope: '/apps/art-1/',
      start_url: '/apps/art-1/?mode=focused',
      display: 'standalone',
      name: 'Weather',
      short_name: 'Weather',
    });
    expect(manifest?.start_url.startsWith(manifest?.scope ?? '?')).toBe(true);
  });

  it('gives every app its own identity, so two installs never collapse into one', () => {
    expect(buildAppManifest('a', 'X')?.id).not.toBe(buildAppManifest('b', 'X')?.id);
    expect(buildAppManifest('a', 'X')?.id).not.toBe('/');
  });

  it.each(['', 'a/b', '../x', 'a b', 'a.b', 'a%2Fb', 'x'.repeat(65), 'é'])('refuses the id %j', (id) => {
    expect(buildAppManifest(id, 'Weather')).toBeNull();
  });

  it('accepts the longest id the desktop shell accepts', () => {
    expect(buildAppManifest('a'.repeat(64), 'Weather')).not.toBeNull();
  });

  it('puts the name on one line without control characters', () => {
    expect(buildAppManifest('a', '  Rain\n\tSoon\u0000 ')?.name).toBe('Rain Soon');
    expect(buildAppManifest('a', 'A\u202EB')?.name).toBe('A B');
  });

  it('shortens by characters, never splitting one', () => {
    const long = '天气'.repeat(40);

    const manifest = buildAppManifest('a', long);

    expect(Array.from(manifest?.name ?? '')).toHaveLength(45);
    expect(Array.from(manifest?.short_name ?? '')).toHaveLength(12);
    expect(buildAppManifest('a', '😀'.repeat(50))?.name).toBe('😀'.repeat(45));
  });

  it('falls back to the product name when none is given', () => {
    expect(buildAppManifest('a', null)?.name).toBe('MyrmAgent');
    expect(buildAppManifest('a', '   ')?.name).toBe('MyrmAgent');
  });

  it('serialises the name as data, so a hostile one cannot become anything else', () => {
    const json = JSON.stringify(buildAppManifest('a', '"}</script><img src=x onerror=1>'));

    expect(JSON.parse(json).name).toBe('"}</script><img src=x onerror=1>');
  });
});

describe('appManifestHref', () => {
  it('carries the name in the address, encoded', () => {
    expect(appManifestHref('art-1', 'Rain & Sun')).toBe('/apps/art-1/manifest.webmanifest?name=Rain%20%26%20Sun');
  });

  it('is an address the open-path check recognises', () => {
    const [path] = appManifestHref('art-1', 'Weather').split('?');

    expect(isAppManifestPath(path)).toBe(true);
  });
});

describe('isAppManifestPath', () => {
  it('opens the manifest file of an app', () => {
    expect(isAppManifestPath('/apps/art-1/manifest.webmanifest')).toBe(true);
    expect(isAppManifestPath(`/apps/${'a'.repeat(64)}/manifest.webmanifest`)).toBe(true);
  });

  it.each([
    '/apps/art-1',
    '/apps/art-1/',
    '/apps/art-1/manifest.webmanifest/',
    '/apps/art-1/manifest.webmanifest/extra',
    '/apps/art-1/other.webmanifest',
    '/apps//manifest.webmanifest',
    '/apps/a/b/manifest.webmanifest',
    '/apps/../manifest.webmanifest',
    '/apps/art-1/manifest.json',
    '/manifest.webmanifest',
    `/apps/${'a'.repeat(65)}/manifest.webmanifest`,
    '/x/apps/art-1/manifest.webmanifest',
  ])('keeps %s behind the session', (path) => {
    expect(isAppManifestPath(path)).toBe(false);
  });
});
