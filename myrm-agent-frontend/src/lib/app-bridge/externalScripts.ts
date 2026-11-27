/**
 * [INPUT]
 * - (none)
 *
 * [OUTPUT]
 * - ExternalScript / listExternalScripts: the scripts an app page loads from other sites, with whether each is locked.
 *
 * [POS]
 * Input of the drift notice in the permission dialog. A permission covers one version of the page, but a script the page
 * loads from another site can change without a new request. The notice lists those sites and marks the scripts nothing
 * locks: an `integrity` hash locks the content, and so does an exact version in the address; a floating address
 * (`@latest`, `@3`, no version at all) does not. The notice only informs; the page's content policy already limits
 * which sites can serve scripts, and blocking would break ordinary library use.
 */

export interface ExternalScript {
  /** Lower-cased host name; the dialog groups and shows scripts by it. */
  host: string;
  /** Neither an `integrity` hash nor an exact version in the address locks the content. */
  mayChange: boolean;
}

const MAX_LISTED = 20;
const INTEGRITY = /^sha(?:256|384|512)-/i;
const EXACT_VERSION = /(?:\/|@)v?\d+\.\d+\.\d+(?:[-+.\w]*)?(?:\/|$)/;

function externalUrl(source: string): URL | null {
  try {
    // A protocol-relative address is resolved against https, the only scheme the page's content policy lets through.
    const url = new URL(source.startsWith('//') ? `https:${source}` : source);
    return (url.protocol === 'https:' || url.protocol === 'http:') && url.hostname ? url : null;
  } catch {
    return null;
  }
}

export function listExternalScripts(html: string): ExternalScript[] {
  const found = new Map<string, ExternalScript>();
  for (const element of new DOMParser().parseFromString(html, 'text/html').querySelectorAll('script[src]')) {
    const url = externalUrl((element.getAttribute('src') ?? '').trim());
    if (!url) {
      continue;
    }
    const locked = INTEGRITY.test((element.getAttribute('integrity') ?? '').trim()) || EXACT_VERSION.test(url.pathname);
    const host = url.hostname.toLowerCase();
    // One host can serve a locked and a floating script; the floating one is what the user must see.
    found.set(host, { host, mayChange: (found.get(host)?.mayChange ?? false) || !locked });
  }
  return [...found.values()].sort((a, b) => a.host.localeCompare(b.host)).slice(0, MAX_LISTED);
}
