/**
 * [INPUT]
 * - @tauri-apps/api/core::invoke (POS: Tauri IPC)
 *
 * [OUTPUT]
 * - hasAppLauncher: whether the app has a system launcher (Spotlight / Start menu / application menu entry).
 * - createAppLauncher: adds (or replaces) the launcher, named after the app.
 * - removeAppLauncher: takes the launcher away.
 *
 * [POS]
 * Desktop-only. The launcher only opens `myrmagent://app/<id>`; the shell writes it into the user's own application
 * folder. Each call reports success as a boolean and never throws, so the menu can show a failure instead of breaking.
 */

async function call<T>(command: string, args: Record<string, unknown>): Promise<{ value: T } | null> {
  try {
    const { invoke } = await import('@tauri-apps/api/core');
    return { value: await invoke<T>(command, args) };
  } catch (error) {
    console.error(`${command} failed:`, error);
    return null;
  }
}

export async function hasAppLauncher(artifactId: string): Promise<boolean> {
  return (await call<boolean>('has_app_launcher', { artifactId }))?.value === true;
}

export async function createAppLauncher(artifactId: string, name: string): Promise<boolean> {
  return (await call('create_app_launcher', { artifactId, name })) !== null;
}

export async function removeAppLauncher(artifactId: string): Promise<boolean> {
  return (await call('remove_app_launcher', { artifactId })) !== null;
}
