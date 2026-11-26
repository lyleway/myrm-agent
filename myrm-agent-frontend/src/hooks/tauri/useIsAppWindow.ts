'use client';

/**
 * [INPUT]
 * - @/lib/deploy-mode::isTauriRuntime (POS: Deployment mode and base-URL resolution layer)
 *
 * [OUTPUT]
 * - useIsAppWindow: whether this page is the standalone desktop window of an installed app.
 *
 * [POS]
 * Detects the app's own desktop window. That window hosts only the app, so the app shell stands down there:
 * app-wide listeners and prompts belong to the main window.
 */

import { useSyncExternalStore } from 'react';
import { isTauriRuntime } from '@/lib/deploy-mode';

const subscribeNever = () => () => undefined;

function readIsAppWindow(): boolean {
  const { pathname, search } = window.location;
  return isTauriRuntime() && pathname.startsWith('/apps/') && new URLSearchParams(search).get('mode') === 'focused';
}

/**
 * Whether this page is the standalone desktop window of an installed app (`/apps/<id>?mode=focused` in Tauri).
 * The window is created with that address and keeps it, so it is read once per mount; the server render and
 * hydration see `false`.
 */
export function useIsAppWindow(): boolean {
  return useSyncExternalStore(subscribeNever, readIsAppWindow, () => false);
}
