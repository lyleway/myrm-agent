/**
 * [INPUT]
 * - (none)
 *
 * [OUTPUT]
 * - MAX_SHORTCUTS: how many key combinations one app may declare.
 * - normalizeAccelerator: the canonical spelling of one global key combination, or `null` when it is not allowed.
 *
 * [POS]
 * The key-combination rule shared by the declaration parser, the permission dialog and the bridge. The server and the
 * desktop shell apply the same rule (`capabilities.normalize_accelerator`, `app_surface::accelerator`), so the
 * combination the user reads in the dialog is the one that gets registered. A combination needs a modifier other than
 * Shift, so a page cannot take over plain typing.
 */

export const MAX_SHORTCUTS = 4;

const MODIFIERS = ['CommandOrControl', 'Control', 'Alt', 'Shift'] as const;
const KEY = /^(?:[A-Z0-9]|F(?:[1-9]|1[0-2]))$/;

export function normalizeAccelerator(raw: string): string | null {
  const parts = raw.split('+').map((part) => part.trim());
  const key = (parts.pop() ?? '').toUpperCase();
  if (!KEY.test(key) || parts.length === 0) {
    return null;
  }
  const chosen = new Set<(typeof MODIFIERS)[number]>();
  for (const part of parts) {
    const modifier = MODIFIERS.find((name) => name.toLowerCase() === part.toLowerCase());
    if (!modifier || chosen.has(modifier)) {
      return null;
    }
    chosen.add(modifier);
  }
  if (!MODIFIERS.some((name) => name !== 'Shift' && chosen.has(name))) {
    return null;
  }
  return [...MODIFIERS.filter((name) => chosen.has(name)), key].join('+');
}
