/**
 * [INPUT]
 * - @tauri-apps/api/core::invoke (POS: Tauri IPC)
 * - @tauri-apps/api/webviewWindow::getCurrentWebviewWindow (POS: this window's own event listeners)
 *
 * [OUTPUT]
 * - appSurface: the `AppSurfaceDeps` of an app's own desktop window (tray item, global key combinations, floating).
 * - releaseAppSurface: gives back the tray item and every key combination of the calling app.
 * - onAppSurfaceEvent: receives a pressed key combination and the tray menu's "Permissions" item.
 *
 * [POS]
 * Desktop-only. The commands act on the calling window's app (the shell derives it from the window label), so a page
 * cannot touch another app's tray item or key combinations. Every call reports success as a value and never throws; a
 * refused or failed call is `false` (`'failed'`), a combination another app or the system holds is `'conflict'`.
 */

import type { AppSurfaceDeps } from '@/lib/app-bridge/hostTypes';

async function call(command: string, args: Record<string, unknown>): Promise<boolean> {
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke(command, args);
    return true;
  } catch (error) {
    console.error(`${command} failed:`, error);
    return false;
  }
}

async function registerShortcut(accelerator: string): Promise<'ok' | 'conflict' | 'failed'> {
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    await invoke('register_app_shortcut', { accelerator });
    return 'ok';
  } catch (error) {
    // The shell reports a taken combination as the exact string `conflict`.
    if (error === 'conflict') {
      return 'conflict';
    }
    console.error('register_app_shortcut failed:', error);
    return 'failed';
  }
}

export const appSurface: AppSurfaceDeps = {
  setTray: (title, tooltip) => call('set_app_tray', { title, tooltip }),
  clearTray: () => call('clear_app_tray', {}),
  registerShortcut,
  unregisterShortcut: (accelerator) => call('unregister_app_shortcut', { accelerator }),
  setFloat: (on) => call('set_app_window_always_on_top', { alwaysOnTop: on }),
};

/** Called before an app's window is closed for good (uninstall): a tray item would otherwise keep the window alive. */
export async function releaseAppSurface(): Promise<void> {
  await Promise.all([appSurface.clearTray(), appSurface.unregisterShortcut(null)]);
}

export interface AppSurfaceEvents {
  onShortcut: (accelerator: string) => void;
  onOpenPermissions: () => void;
}

/**
 * Subscribes to the shell's events for this window; resolves to the unsubscribe function.
 * The shell sends them to this window's label, which only a window-scoped listener receives (the plain `listen` is
 * for untargeted events).
 */
export async function onAppSurfaceEvent({ onShortcut, onOpenPermissions }: AppSurfaceEvents): Promise<() => void> {
  try {
    const { getCurrentWebviewWindow } = await import('@tauri-apps/api/webviewWindow');
    const current = getCurrentWebviewWindow();
    const stops = await Promise.all([
      current.listen<{ accelerator: string }>('app-surface:shortcut', (event) => onShortcut(event.payload.accelerator)),
      current.listen('app-surface:permissions', () => onOpenPermissions()),
    ]);
    return () => stops.forEach((stop) => stop());
  } catch (error) {
    console.error('listening to app surface events failed:', error);
    return () => undefined;
  }
}
