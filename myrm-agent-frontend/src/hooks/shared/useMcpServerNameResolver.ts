'use client';

/**
 * [INPUT]
 * @/store/useConfigStore::mcpConfigs, orgMcpConfigs (POS: the MCP servers of the user and of the organisation)
 * @/lib/utils/mcp-config::mcpServerNameResolver (POS: identity-to-name lookup in the runtime's precedence)
 *
 * [OUTPUT]
 * useMcpServerNameResolver: looks up the name the user gave an MCP server by the identity in a tool name
 *
 * [POS]
 * The model calls `mcp__{identity}__{tool}`, which names no server a person would recognise; the settings do. Every
 * surface that words an MCP tool call (step titles, approval cards, blocked actions) asks this lookup, which keeps one
 * reference until a server list changes.
 */

import { useMemo } from 'react';
import type { McpServerNameResolver } from '@/lib/humanize';
import { mcpServerNameResolver } from '@/lib/utils/mcp-config';
import useConfigStore from '@/store/useConfigStore';

/** The organisation's servers come first: when two share an identity the runtime keeps the organisation's. */
export function useMcpServerNameResolver(): McpServerNameResolver {
  const mcpConfigs = useConfigStore((state) => state.mcpConfigs);
  const orgMcpConfigs = useConfigStore((state) => state.orgMcpConfigs);
  return useMemo(() => mcpServerNameResolver([...orgMcpConfigs, ...mcpConfigs]), [orgMcpConfigs, mcpConfigs]);
}
