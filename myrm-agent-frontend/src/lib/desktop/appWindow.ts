/**
 * [INPUT]
 * - @tauri-apps/api/core::invoke (POS: Tauri IPC)
 *
 * [OUTPUT]
 * - openAppWindow: opens an installed app in its own desktop window (or brings the open one forward).
 * - closeAppWindow: closes the window this page runs in.
 * - setAppWindowAlwaysOnTop: keeps the app window above others, or stops doing so.
 *
 * [POS]
 * Desktop-only window control for installed apps. Each call reports success as a boolean so callers fall back to the
 * in-page route (`/apps/[id]`) when the shell is not Tauri or the command fails; none of them throws.
 */

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

export function openAppWindow(artifactId: string, title: string): Promise<boolean> {
  return call('open_app_window', { artifactId, title });
}

export function closeAppWindow(): Promise<boolean> {
  return call('close_app_window', {});
}

export function setAppWindowAlwaysOnTop(alwaysOnTop: boolean): Promise<boolean> {
  return call('set_app_window_always_on_top', { alwaysOnTop });
}
