/**
 * [INPUT]
 * - ../accelerator::normalizeAccelerator
 *
 * [OUTPUT]
 * - Tests: one spelling per key combination, and nothing that would let a page take over plain typing.
 *
 * [POS]
 * The rule the dialog, the declaration and the shell all apply: what the user reads is what gets registered.
 */

import { describe, expect, it } from 'vitest';
import { normalizeAccelerator } from '../accelerator';

describe('normalizeAccelerator', () => {
  it('spells a combination one way regardless of case, spacing and modifier order', () => {
    expect(normalizeAccelerator('shift + commandorcontrol + k')).toBe('CommandOrControl+Shift+K');
    expect(normalizeAccelerator('CommandOrControl+Shift+K')).toBe('CommandOrControl+Shift+K');
    expect(normalizeAccelerator('alt+control+9')).toBe('Control+Alt+9');
  });

  it('accepts letters, digits and the function keys F1 to F12', () => {
    for (const key of ['A', 'Z', '0', '9', 'F1', 'F9', 'F10', 'F12']) {
      expect(normalizeAccelerator(`Alt+${key}`)).toBe(`Alt+${key}`);
    }
  });

  it('refuses a combination without a modifier other than Shift', () => {
    expect(normalizeAccelerator('K')).toBeNull();
    expect(normalizeAccelerator('F5')).toBeNull();
    expect(normalizeAccelerator('Shift+K')).toBeNull();
  });

  it('refuses keys outside the allowed set', () => {
    for (const key of ['Space', 'Enter', 'Tab', 'Esc', 'Left', 'F0', 'F13', 'AB', '-', '']) {
      expect(normalizeAccelerator(`Alt+${key}`)).toBeNull();
    }
  });

  it('refuses unknown or repeated modifiers', () => {
    expect(normalizeAccelerator('Super+K')).toBeNull();
    expect(normalizeAccelerator('Meta+K')).toBeNull();
    expect(normalizeAccelerator('Alt+Alt+K')).toBeNull();
  });

  it('refuses text that is not a combination at all', () => {
    expect(normalizeAccelerator('')).toBeNull();
    expect(normalizeAccelerator('+')).toBeNull();
    expect(normalizeAccelerator('Alt+')).toBeNull();
  });
});
