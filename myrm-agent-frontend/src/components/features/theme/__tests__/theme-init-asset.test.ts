import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import {
  FUNCTIONAL_ART_WASH_FLOOR,
  FUNCTIONAL_ROUTE_PREFIXES,
  FUNCTIONAL_SURFACE_FLOORS,
  STATIC_APP_SEGMENTS,
} from '@/theme-engine';

const publicScript = readFileSync(resolve(process.cwd(), 'public/theme-init.js'), 'utf8');

describe('public/theme-init.js embeds the theme-engine tables', () => {
  it('reads and writes the storage keys and root attributes the theme engine owns', () => {
    for (const key of [
      'myrm-theme-preinit',
      'myrm-skin',
      'myrm-font',
      'theme-color',
      'data-myrm-theme-profile',
      'data-myrm-theme-layout',
      'data-myrm-theme-scene',
      'data-myrm-theme-art',
      'artPosterUrl',
      'myrm-theme-art-preload',
    ]) {
      expect(publicScript).toContain(key);
    }
  });

  it('embeds every functional route prefix from readability-scene SSOT', () => {
    for (const prefix of FUNCTIONAL_ROUTE_PREFIXES) {
      expect(publicScript).toContain(`'${prefix}'`);
    }
  });

  it('embeds every static app segment from readability-scene SSOT', () => {
    for (const segment of STATIC_APP_SEGMENTS) {
      expect(publicScript).toContain(`'${segment}':1`);
    }
  });

  it('embeds functional surface opacity floors from scene-surfaces SSOT', () => {
    expect(publicScript).toContain(String(FUNCTIONAL_SURFACE_FLOORS.navOpacity));
    expect(publicScript).toContain(String(FUNCTIONAL_SURFACE_FLOORS.sidebarOpacity));
    expect(publicScript).toContain(String(FUNCTIONAL_SURFACE_FLOORS.mainOpacity));
    expect(publicScript).toContain(String(FUNCTIONAL_SURFACE_FLOORS.surfaceOpacity));
    expect(publicScript).toContain(String(FUNCTIONAL_ART_WASH_FLOOR));
  });
});
