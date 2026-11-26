import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// The script the browser really runs: layout.tsx serves it as /theme-init.js before hydration.
const THEME_INIT_SCRIPT = readFileSync(resolve(process.cwd(), 'public/theme-init.js'), 'utf8');

describe('public/theme-init.js', () => {
  it('sets theme-color meta from localStorage theme', () => {
    document.documentElement.innerHTML = '';
    document.head.innerHTML = '<meta name="theme-color" content="#fdfdfb" />';

    localStorage.setItem('theme', 'dark');
    // eslint-disable-next-line no-eval -- the pre-hydration script is a classic script, evaluated as one
    eval(THEME_INIT_SCRIPT);

    expect(document.querySelector('meta[name="theme-color"]')?.getAttribute('content')).toBe('#0a0a0a');
  });

  it('applies preinit snapshot tokens when available', () => {
    document.documentElement.removeAttribute('data-myrm-theme-profile');
    localStorage.setItem(
      'myrm-theme-preinit',
      JSON.stringify({
        profileId: 'preset-teal',
        layoutId: 'nav-rail-focus',
        artOn: true,
        isDark: false,
        primary: '#588e95',
        primaryForeground: '#fbfbf8',
        primaryHover: '#4a7d84',
      }),
    );
    // eslint-disable-next-line no-eval -- the pre-hydration script is a classic script, evaluated as one
    eval(THEME_INIT_SCRIPT);

    expect(document.documentElement.getAttribute('data-myrm-theme-profile')).toBe('preset-teal');
    expect(document.documentElement.getAttribute('data-myrm-theme-layout')).toBe('nav-rail-focus');
    expect(document.documentElement.getAttribute('data-myrm-theme-art')).toBe('on');
    expect(document.documentElement.style.getPropertyValue('--primary')).toBe('#588e95');
  });

  it('applies functional scene tokens for kanban cold start path', () => {
    document.documentElement.innerHTML = '';
    const originalPathname = window.location.pathname;
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, pathname: '/kanban' },
    });
    localStorage.setItem(
      'myrm-theme-preinit',
      JSON.stringify({
        profileId: 'preset-teal',
        layoutId: 'full-bleed',
        sceneId: 'immersive',
        artOn: true,
        isDark: false,
        primary: '#588e95',
        primaryForeground: '#0a0a0a',
        primaryHover: '#4a7d84',
        artWash: 0.25,
      }),
    );
    // eslint-disable-next-line no-eval -- the pre-hydration script is a classic script, evaluated as one
    eval(THEME_INIT_SCRIPT);
    expect(document.documentElement.getAttribute('data-myrm-theme-scene')).toBe('functional');
    expect(document.documentElement.style.getPropertyValue('--myrm-theme-main-opacity')).toBe('0.94');
    expect(Number(document.documentElement.style.getPropertyValue('--myrm-theme-art-wash'))).toBeGreaterThan(0.25);
    Object.defineProperty(window, 'location', {
      configurable: true,
      value: { ...window.location, pathname: originalPathname },
    });
  });

  it('purges legacy skin and font localStorage keys', () => {
    localStorage.setItem('myrm-skin', 'ocean');
    localStorage.setItem('myrm-font', 'system');
    // eslint-disable-next-line no-eval -- the pre-hydration script is a classic script, evaluated as one
    eval(THEME_INIT_SCRIPT);

    expect(localStorage.getItem('myrm-skin')).toBeNull();
    expect(localStorage.getItem('myrm-font')).toBeNull();
  });
});
