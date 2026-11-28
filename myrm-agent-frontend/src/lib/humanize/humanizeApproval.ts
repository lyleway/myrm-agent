import { extractContextFromToolInput } from './extractContext';
import { humanizeToolLine } from './humanizeToolLine';
import type { McpServerNameResolver, TranslateFn } from './types';

export function humanizeApprovalTitle(
  toolName: string,
  toolInput: Record<string, unknown>,
  t: TranslateFn,
  serverName?: McpServerNameResolver,
): string {
  const ctx = extractContextFromToolInput(toolInput);
  return humanizeToolLine(toolName, ctx, t, 'approval', serverName);
}
