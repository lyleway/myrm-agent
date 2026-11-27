/**
 * [INPUT]
 * - ./protocol::BridgeCapability (POS: capability vocabulary of the page-to-host channel)
 * - ./accelerator (POS: the key-combination rule)
 *
 * [OUTPUT]
 * - Declaration / parseDeclaration: what an app page says it needs, read from its own HTML.
 * - GrantRecord / CapabilityStatus / resolveStatuses: whether each declared capability may be used, from the stored decisions.
 * - offeredCapabilities: the declared capabilities a given window can ask the user about.
 * - isExactPublicHost / isSubset: the host-name rule shared with the permission dialog.
 *
 * [POS]
 * The permission model of an installed app. A page declares capabilities in a JSON block; the user's decision is stored
 * per capability together with the scope they approved. A declaration the user has not seen (new capability, or hosts or
 * key combinations beyond what they approved) is `ask`; an earlier decision keeps applying while the declaration stays
 * within its scope. Host names are exact public names so the list the user reads is the list the server enforces.
 */

import { MAX_SHORTCUTS, normalizeAccelerator } from './accelerator';
import { BRIDGE_CAPABILITIES, isDesktopCapability, type BridgeCapability } from './protocol';

export const DECLARATION_ELEMENT_ID = 'myrm-app';
export const MAX_FETCH_HOSTS = 20;

/** Mirrors the server rule: dotted names ending in an alphabetic label (no localhost, IP literals or ports). */
const HOST_NAME = /^(?=.{1,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$/;

export function isExactPublicHost(name: string): boolean {
  return HOST_NAME.test(name);
}

export interface Declaration {
  notify: boolean;
  /** Sorted, lower-cased, de-duplicated; empty when `fetch` is not declared. */
  fetchHosts: string[];
  ai: boolean;
  tray: boolean;
  float: boolean;
  /** Canonical and sorted; empty when `shortcut` is not declared. */
  shortcuts: string[];
}

export const EMPTY_DECLARATION: Declaration = {
  notify: false,
  fetchHosts: [],
  ai: false,
  tray: false,
  float: false,
  shortcuts: [],
};

function parseHosts(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0 || !value.every((item) => typeof item === 'string')) {
    return [];
  }
  const names = [...new Set(value.map((item: string) => item.trim().toLowerCase()))];
  if (names.length > MAX_FETCH_HOSTS || !names.every(isExactPublicHost)) {
    return [];
  }
  return names.sort();
}

function parseShortcuts(value: unknown): string[] {
  if (!Array.isArray(value) || value.length === 0 || !value.every((item) => typeof item === 'string')) {
    return [];
  }
  const combos = value.map((item: string) => normalizeAccelerator(item));
  if (combos.includes(null)) {
    return [];
  }
  const names = [...new Set(combos as string[])];
  return names.length > MAX_SHORTCUTS ? [] : names.sort();
}

/**
 * Reads `<script type="application/json" id="myrm-app">{"notify": true, "ai": true, "fetch": ["api.example.com"]}</script>`.
 * The parse is inert (no script runs). A malformed block, a `fetch` list with anything but exact host names, or a
 * `shortcut` list with a combination that is not allowed, declares nothing for that capability: the page then simply
 * cannot use it.
 */
export function parseDeclaration(html: string): Declaration {
  const element = new DOMParser()
    .parseFromString(html, 'text/html')
    .querySelector(`script[type="application/json"]#${DECLARATION_ELEMENT_ID}`);
  if (!element?.textContent) {
    return EMPTY_DECLARATION;
  }
  let block: unknown;
  try {
    block = JSON.parse(element.textContent);
  } catch {
    return EMPTY_DECLARATION;
  }
  if (typeof block !== 'object' || block === null) {
    return EMPTY_DECLARATION;
  }
  const { notify, fetch: fetchHosts, ai, tray, float, shortcut } = block as Record<string, unknown>;
  return {
    notify: notify === true,
    fetchHosts: parseHosts(fetchHosts),
    ai: ai === true,
    tray: tray === true,
    float: float === true,
    shortcuts: parseShortcuts(shortcut),
  };
}

/** What the page declared for a capability: `null` when it did not, `[]` for a capability without scope. */
export function declaredScope(capability: BridgeCapability, declaration: Declaration): string[] | null {
  switch (capability) {
    case 'notify':
      return declaration.notify ? [] : null;
    case 'ai':
      return declaration.ai ? [] : null;
    case 'tray':
      return declaration.tray ? [] : null;
    case 'float':
      return declaration.float ? [] : null;
    case 'fetch':
      return declaration.fetchHosts.length > 0 ? declaration.fetchHosts : null;
    case 'shortcut':
      return declaration.shortcuts.length > 0 ? declaration.shortcuts : null;
  }
}

export function isSubset(declared: readonly string[], approved: readonly string[]): boolean {
  return declared.every((name) => approved.includes(name));
}

export interface GrantRecord {
  capability: BridgeCapability;
  decision: 'allow' | 'deny';
  scope: string[];
  version_sha256: string;
}

export type CapabilityStatus = 'undeclared' | 'ask' | 'allowed' | 'denied';

export function resolveStatuses(
  declaration: Declaration,
  grants: readonly GrantRecord[],
): Record<BridgeCapability, CapabilityStatus> {
  const statuses = {} as Record<BridgeCapability, CapabilityStatus>;
  for (const capability of BRIDGE_CAPABILITIES) {
    const declared = declaredScope(capability, declaration);
    const grant = grants.find((candidate) => candidate.capability === capability);
    if (declared === null) {
      statuses[capability] = 'undeclared';
    } else if (!grant || !isSubset(declared, grant.scope)) {
      statuses[capability] = 'ask';
    } else {
      statuses[capability] = grant.decision === 'allow' ? 'allowed' : 'denied';
    }
  }
  return statuses;
}

/**
 * The declared capabilities this window can ask about. The tray item, key combinations and floating act on the desktop
 * around the app's own window, so anywhere else they are neither listed nor counted as undecided.
 */
export function offeredCapabilities(
  statuses: Record<BridgeCapability, CapabilityStatus>,
  desktopAvailable: boolean,
): BridgeCapability[] {
  return BRIDGE_CAPABILITIES.filter(
    (capability) => statuses[capability] !== 'undeclared' && (desktopAvailable || !isDesktopCapability(capability)),
  );
}
