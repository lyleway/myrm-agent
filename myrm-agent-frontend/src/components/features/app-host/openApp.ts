/**
 * [INPUT]
 * - @/lib/deploy-mode::isTauriRuntime (POS: desktop shell detection)
 * - @/lib/desktop/appWindow::openAppWindow (POS: desktop window control for installed apps)
 *
 * [OUTPUT]
 * - openApp: opens an installed app: its own window on desktop, the `/apps/[id]` page everywhere else.
 *
 * [POS]
 * Single entry for "open this app" so the apps list and the install button behave the same.
 */

import { isTauriRuntime } from '@/lib/deploy-mode';
import { openAppWindow } from '@/lib/desktop/appWindow';

export async function openApp(artifactId: string, name: string, navigate: (href: string) => void): Promise<void> {
  if (isTauriRuntime() && (await openAppWindow(artifactId, name))) {
    return;
  }
  navigate(`/apps/${artifactId}`);
}
