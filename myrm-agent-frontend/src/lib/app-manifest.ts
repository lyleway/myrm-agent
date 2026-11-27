/**
 * [INPUT]
 * - (none)
 *
 * [OUTPUT]
 * - isAppManifestPath: whether a request path is an installed app's manifest (the only app path served without a session).
 * - appManifestHref: the manifest address the app's host page points the browser at.
 * - buildAppManifest: the web app manifest of one installed app, or `null` for an id that cannot be one.
 *
 * [POS]
 * Per-app PWA identity for browsers: "Install" or "Add to Dock" on `/apps/<id>/` creates a window for that app alone
 * instead of the whole site (`/manifest.json` has `id: "/"`). The browser fetches a manifest without credentials, so the
 * route serves it without a session; it therefore holds nothing the requester did not supply: the id is checked for
 * shape only and the name comes in the address (the host page, which has the session, puts it there).
 */

const APP_ID = /^[A-Za-z0-9_-]{1,64}$/;
const MANIFEST_PATH = /^\/apps\/([A-Za-z0-9_-]{1,64})\/manifest\.webmanifest$/;
const NAME_MAX = 45;
const SHORT_NAME_MAX = 12;
const FALLBACK_NAME = 'MyrmAgent';

export interface AppManifest {
  id: string;
  name: string;
  short_name: string;
  start_url: string;
  scope: string;
  display: 'standalone';
  background_color: string;
  theme_color: string;
  icons: { src: string; sizes: string; type: string; purpose: string }[];
}

export function isAppManifestPath(pathname: string): boolean {
  return MANIFEST_PATH.test(pathname);
}

export function appManifestHref(artifactId: string, name: string): string {
  return `/apps/${artifactId}/manifest.webmanifest?name=${encodeURIComponent(name)}`;
}

/** One line, no control characters, at most `NAME_MAX` characters (counted as characters, not UTF-16 units). */
function cleanName(raw: string | null): string {
  const text = (raw ?? '').replace(/[\p{Cc}\p{Cf}]/gu, ' ').replace(/\s+/g, ' ').trim();
  return Array.from(text).slice(0, NAME_MAX).join('') || FALLBACK_NAME;
}

export function buildAppManifest(artifactId: string, rawName: string | null): AppManifest | null {
  if (!APP_ID.test(artifactId)) {
    return null;
  }
  const name = cleanName(rawName);
  const scope = `/apps/${artifactId}/`;
  return {
    id: scope,
    name,
    short_name: Array.from(name).slice(0, SHORT_NAME_MAX).join(''),
    // The window shows the app alone: `mode=focused` leaves out the app shell around the page.
    start_url: `${scope}?mode=focused`,
    scope,
    display: 'standalone',
    background_color: '#0a0a0a',
    theme_color: '#0a0a0a',
    icons: [
      { src: '/icons/icon-192x192.png', sizes: '192x192', type: 'image/png', purpose: 'any maskable' },
      { src: '/icons/icon-512x512.png', sizes: '512x512', type: 'image/png', purpose: 'any maskable' },
    ],
  };
}
