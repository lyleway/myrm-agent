/**
 * [INPUT]
 * - ./protocol::BRIDGE_CAPABILITIES (POS: capability vocabulary)
 *
 * [OUTPUT]
 * - createCallLimiter: sliding-window admission per capability.
 *
 * [POS]
 * Bounds how often a page can call each capability. The numbers sit above the server's own limits, so for allowed calls
 * the server stays the arbiter; this limiter bounds the chatter of refused calls and the pace of desktop calls.
 */

import { BRIDGE_CAPABILITIES, type BridgeCapability } from './protocol';

const WINDOW_MS = 60_000;
const CALL_LIMITS: Record<BridgeCapability, number> = {
  notify: 5,
  fetch: 120,
  ai: 40,
  tray: 30,
  shortcut: 20,
  float: 30,
};

type Admission = 'admit' | 'first_over' | 'over';

/** Sliding window per capability; `first_over` lets the caller log one refusal per window instead of one per call. */
export function createCallLimiter(now: () => number) {
  const calls = Object.fromEntries(BRIDGE_CAPABILITIES.map((capability) => [capability, [] as number[]])) as Record<
    BridgeCapability,
    number[]
  >;
  const refused = Object.fromEntries(BRIDGE_CAPABILITIES.map((capability) => [capability, false])) as Record<
    BridgeCapability,
    boolean
  >;
  return (capability: BridgeCapability): Admission => {
    const cutoff = now() - WINDOW_MS;
    const recent = calls[capability].filter((at) => at > cutoff);
    if (recent.length >= CALL_LIMITS[capability]) {
      calls[capability] = recent;
      const first = !refused[capability];
      refused[capability] = true;
      return first ? 'first_over' : 'over';
    }
    recent.push(now());
    calls[capability] = recent;
    refused[capability] = false;
    return 'admit';
  };
}
