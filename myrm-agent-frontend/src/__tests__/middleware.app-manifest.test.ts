/**
 * [INPUT]
 * - @/middleware::middleware
 *
 * [OUTPUT]
 * - Tests: in sandbox mode a visitor without a session reaches an installed app's manifest file and nothing else of the app.
 *
 * [POS]
 * Browsers fetch a manifest without credentials, so its path is open; the app page next to it must stay behind the login.
 */

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { NextRequest } from 'next/server';
import { middleware } from '@/middleware';

const visit = (path: string) => middleware(new NextRequest(`https://app.example.com${path}`));

describe('middleware: installed app manifest in sandbox mode', () => {
  beforeEach(() => {
    vi.stubEnv('NEXT_PUBLIC_DEPLOY_MODE', 'sandbox');
  });

  afterEach(() => {
    vi.unstubAllEnvs();
  });

  it('lets the manifest file through without a session', () => {
    const response = visit('/apps/art-1/manifest.webmanifest?name=Weather');

    expect(response.status).toBe(200);
    expect(response.headers.get('location')).toBeNull();
  });

  it.each([
    '/apps/art-1',
    '/apps/art-1/',
    '/apps/art-1/manifest.webmanifest/extra',
    '/apps/art-1/other',
    '/apps/a/b/manifest.webmanifest',
  ])('keeps %s behind the login', (path) => {
    const response = visit(path);

    expect(response.status).toBe(307);
    expect(new URL(response.headers.get('location') ?? '').pathname).toBe('/auth/login');
  });
});
