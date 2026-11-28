import type { McpServerNameResolver, TranslateFn } from './types';
import type { HumanizeContext } from './extractContext';
import { parseMcpToolName } from './extractContext';
import { platformLabelFromTarget } from './scopeNote';
import { displayUrlHost } from './pathUtils';

type ToolLineMode = 'progress' | 'approval' | 'ask';

/** The identity of a server whose name has nothing a person can read: `unnamed` or `unnamed_` and a 6-digit hash. */
const OPAQUE_SERVER_IDENTITY = /^unnamed(?:_[0-9a-f]{6})?$/;

function normalizeToolKey(toolName: string): string {
  return toolName.replace(/_tool$/, '').replace(/-/g, '_');
}

function isMissingTranslation(key: string, hit: string): boolean {
  return !hit || hit === key || hit.endsWith(`.${key}`) || hit.endsWith(key);
}

/** The server as the user knows it: the name they gave it, else its identity when that reads, else a generic label. */
function serverLabel(identity: string, t: TranslateFn, serverName?: McpServerNameResolver): string {
  const named = serverName?.(identity);
  if (named) {
    return named;
  }
  if (!OPAQUE_SERVER_IDENTITY.test(identity)) {
    return identity;
  }
  const generic = t('fallback.service');
  return isMissingTranslation('fallback.service', generic) ? identity : generic;
}

/** An MCP tool belongs to its server whatever words its own name shares with a built-in tool, so it is worded first. */
function humanizeMcpToolLine(
  toolName: string,
  t: TranslateFn,
  mode: ToolLineMode,
  serverName?: McpServerNameResolver,
): string | undefined {
  if (!toolName.startsWith('mcp__')) {
    return undefined;
  }
  const { server, tool } = parseMcpToolName(toolName);
  if (!server || !tool) {
    return undefined;
  }
  return translate(t, `${mode}.mcp`, { server: serverLabel(server, t, serverName), tool: tool.replace(/_/g, ' ') });
}

function translate(t: TranslateFn, key: string, values?: Record<string, string>): string {
  const hit = t(key, values);
  if (!isMissingTranslation(key, hit)) {
    return hit;
  }
  const toolLabel = values?.tool ?? values?.filename ?? values?.name ?? key;
  const used = t('fallback.used_tool', { tool: String(toolLabel) });
  if (!isMissingTranslation('fallback.used_tool', used)) {
    return used;
  }
  return String(toolLabel);
}

/**
 * Human-readable one-liner for tools in progress / approval / ask modes. `serverName` gives the name the user chose for
 * the MCP server behind a tool name, which carries only its identity.
 */
export function humanizeToolLine(
  toolName: string,
  ctx: HumanizeContext,
  t: TranslateFn,
  mode: ToolLineMode = 'progress',
  serverName?: McpServerNameResolver,
): string {
  const mcpLine = humanizeMcpToolLine(toolName, t, mode, serverName);
  if (mcpLine !== undefined) {
    return mcpLine;
  }
  const normalized = normalizeToolKey(toolName);
  const prefix = mode;
  const fallbackFile = t('fallback.file');
  const filename = ctx.filename ?? (isMissingTranslation('fallback.file', fallbackFile) ? 'file' : fallbackFile);

  if (normalized.includes('file_read') || normalized === 'read_file') {
    return translate(t, `${prefix}.file_read`, { filename });
  }
  if (
    normalized.includes('file_write') ||
    normalized.includes('file_edit') ||
    normalized.includes('file_editor') ||
    normalized.includes('text_editor') ||
    normalized === 'write_file' ||
    normalized === 'replace_in_file'
  ) {
    const askKey = mode === 'ask' ? 'file_write_ask' : 'file_write';
    return translate(t, `${prefix}.${askKey}`, { filename });
  }
  if (toolName.startsWith('browser_') || normalized.startsWith('browser_')) {
    if (ctx.url) {
      return translate(t, `${prefix}.browser_url`, { url: displayUrlHost(ctx.url) });
    }
    return translate(t, `${prefix}.browser_generic`, {});
  }
  if (normalized.includes('bash') || normalized === 'execute_code' || normalized === 'run_shell') {
    if (ctx.description) {
      return translate(t, `${prefix}.shell_with_desc`, { description: ctx.description });
    }
    if (ctx.command) {
      return translate(t, `${prefix}.shell`, { command: ctx.command });
    }
    return translate(t, `${prefix}.shell_generic`, {});
  }
  if (normalized.includes('web_search') || normalized === 'grep') {
    if (ctx.query) {
      return translate(t, `${prefix}.web_search`, { query: ctx.query });
    }
    return translate(t, `${prefix}.web_search_generic`, {});
  }
  if (normalized.includes('web_fetch')) {
    if (ctx.url) {
      return translate(t, `${prefix}.web_fetch`, { url: displayUrlHost(ctx.url) });
    }
    return translate(t, `${prefix}.web_fetch_generic`, {});
  }
  if (normalized.includes('memory')) {
    return translate(t, `${prefix}.memory_generic`, {});
  }
  if (normalized.includes('kanban') || normalized === 'todo_write') {
    if (ctx.text) {
      return translate(t, `${prefix}.kanban`, { task: ctx.text });
    }
    return translate(t, `${prefix}.kanban_generic`, {});
  }
  if (normalized === 'save_skill' || toolName === 'save_skill_tool') {
    if (ctx.skillName) {
      return translate(t, `${prefix}.save_skill`, { name: ctx.skillName });
    }
    return translate(t, `${prefix}.save_skill_generic`, {});
  }
  if (normalized === 'skill_manage' || toolName === 'skill_manage_tool') {
    const action = typeof ctx.action === 'string' ? ctx.action.trim() : '';
    if (action && action !== 'save') {
      return translate(t, `${prefix}.skill_generic`, {});
    }
    if (ctx.skillName) {
      return translate(t, `${prefix}.save_skill`, { name: ctx.skillName });
    }
    return translate(t, `${prefix}.save_skill_generic`, {});
  }
  if (normalized === 'channel_notify') {
    // The destination is the name the user configured, shown as the model gave it: what is approved is what is sent.
    return ctx.destination
      ? translate(t, `${prefix}.send_message`, { destination: ctx.destination })
      : translate(t, `${prefix}.send_message_generic`, {});
  }
  if (
    normalized.includes('send_message') ||
    normalized.includes('send_file') ||
    toolName === 'channel_send' ||
    toolName === 'send_message_tool' ||
    toolName === 'send_file_tool'
  ) {
    if (ctx.target) {
      const destination = platformLabelFromTarget(ctx.target);
      return translate(t, `${prefix}.send_message`, { destination });
    }
    return translate(t, `${prefix}.send_message_generic`, {});
  }
  if (normalized.includes('skill')) {
    if (ctx.skillName) {
      return translate(t, `${prefix}.skill`, { name: ctx.skillName });
    }
    return translate(t, `${prefix}.skill_generic`, {});
  }
  const label = toolName
    .replace(/_tool$/, '')
    .replace(/_/g, ' ')
    .replace(/\b\w/g, (c) => c.toUpperCase());
  // Ask tense describes something that never ran (declined, cancelled or blocked), so it must not claim "Used".
  return translate(t, mode === 'ask' ? 'ask.tool_generic' : 'fallback.used_tool', { tool: label });
}
