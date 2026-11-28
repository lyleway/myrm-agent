/**
 * Human-readable tool / approval presentation types.
 *
 * [POS] FE-only SSOT for ProgressSteps titles and approval headlines.
 */

export type TranslateFn = (key: string, values?: Record<string, string | number>) => string;

/** The name the user gave an MCP server, from the identity its tool names carry (`mcp__{identity}__{tool}`). */
export type McpServerNameResolver = (identity: string) => string | undefined;

export type ApprovalSurface = 'compact' | 'full';

export type ScopeNote = {
  text: string;
  external: boolean;
};
