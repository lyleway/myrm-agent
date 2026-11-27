'use client';

/**
 * [INPUT]
 * - @/lib/desktop/appLauncher (POS: the shell's launcher commands)
 * - @/lib/deploy-mode::isTauriRuntime (POS: whether this is the desktop shell)
 *
 * [OUTPUT]
 * - useAppLauncher: `{ supported, present, toggle }` for one app's system launcher; `toggle` adds or removes it and
 *   reports the result as a toast key.
 *
 * [POS]
 * The launcher is a desktop-only convenience of the host page's menu. `present` is `null` until the shell has answered,
 * so the menu never shows a state it has not read.
 */

import { useCallback, useEffect, useState, useSyncExternalStore } from 'react';
import { isTauriRuntime } from '@/lib/deploy-mode';
import { createAppLauncher, hasAppLauncher, removeAppLauncher } from '@/lib/desktop/appLauncher';

const subscribeNever = () => () => undefined;

export type LauncherOutcome = 'added' | 'removed' | 'failed';

export function useAppLauncher(artifactId: string, appName: string) {
  const supported = useSyncExternalStore(subscribeNever, isTauriRuntime, () => false);
  const [present, setPresent] = useState<boolean | null>(null);

  useEffect(() => {
    if (!supported) {
      return;
    }
    let live = true;
    void hasAppLauncher(artifactId).then((has) => {
      if (live) {
        setPresent(has);
      }
    });
    return () => {
      live = false;
    };
  }, [supported, artifactId]);

  const toggle = useCallback(async (): Promise<LauncherOutcome> => {
    const adding = present !== true;
    const done = adding ? await createAppLauncher(artifactId, appName) : await removeAppLauncher(artifactId);
    if (!done) {
      return 'failed';
    }
    setPresent(adding);
    return adding ? 'added' : 'removed';
  }, [appName, artifactId, present]);

  return { supported, present, toggle };
}
