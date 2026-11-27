'use client';

/**
 * [INPUT]
 * - @/lib/app-manifest::appManifestHref (POS: the app's own manifest address)
 * - @/lib/deploy-mode::isTauriRuntime (POS: whether this is the desktop shell)
 *
 * [OUTPUT]
 * - useAppManifest: points the page's `<link rel="manifest">` at this app's manifest while the host page is shown.
 *
 * [POS]
 * Makes the browser's "Install" / "Add to Dock" on `/apps/<id>` create a window for this app instead of the whole site.
 * The link goes back to the site manifest when the page goes away. The desktop shell has its own app windows and no
 * install prompt, so it leaves the link alone.
 */

import { useEffect } from 'react';
import { appManifestHref } from '@/lib/app-manifest';
import { isTauriRuntime } from '@/lib/deploy-mode';

export function useAppManifest(artifactId: string, appName: string | undefined): void {
  useEffect(() => {
    const link = document.querySelector<HTMLLinkElement>('link[rel="manifest"]');
    if (!appName || !link || isTauriRuntime()) {
      return;
    }
    const siteManifest = link.getAttribute('href');
    link.setAttribute('href', appManifestHref(artifactId, appName));
    return () => {
      if (siteManifest !== null) {
        link.setAttribute('href', siteManifest);
      }
    };
  }, [artifactId, appName]);
}
