import type { McpServerNameResolver } from './types';

/** The identity of a server whose name has nothing a person can read: `unnamed` or `unnamed_` and a 6-digit hash. */
const OPAQUE_SERVER_IDENTITY = /^unnamed(?:_[0-9a-f]{6})?$/;

/**
 * The server behind an MCP tool name as the user knows it: the name they gave it, else its identity when that reads,
 * else `undefined`, because an identity such as `unnamed_dd4acd` names no server to a person.
 */
export function readableMcpServerName(identity: string, serverName?: McpServerNameResolver): string | undefined {
  const named = serverName?.(identity);
  if (named) {
    return named;
  }
  return identity && !OPAQUE_SERVER_IDENTITY.test(identity) ? identity : undefined;
}
