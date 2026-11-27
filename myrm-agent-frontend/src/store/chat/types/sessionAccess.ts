/**
 * [OUTPUT]
 * SessionAccessRoot, normalizeSessionAccessRoots.
 *
 * [POS]
 * 会话目录授权根的契约与 API JSON 解析；路径原样保留，撤销时原样发回。
 */

/** Session-scoped HITL directory grant persisted on the chat row. */
export interface SessionAccessRoot {
  path: string;
  writable: boolean;
  label?: string;
  source?: string;
}

/**
 * The roots the server stores, as the chat shows and revokes them. A path is kept exactly as stored because revoking
 * sends it back: a folder whose name ends in a space is not the folder without it. Only the display text is trimmed.
 */
export function normalizeSessionAccessRoots(raw: unknown): SessionAccessRoot[] {
  if (!Array.isArray(raw)) {
    return [];
  }
  const roots: SessionAccessRoot[] = [];
  for (const item of raw) {
    if (!item || typeof item !== 'object') {
      continue;
    }
    const path = (item as { path?: unknown }).path;
    if (typeof path !== 'string' || !path.trim()) {
      continue;
    }
    const writable = (item as { writable?: unknown }).writable;
    const label = (item as { label?: unknown }).label;
    const source = (item as { source?: unknown }).source;
    roots.push({
      path,
      writable: typeof writable === 'boolean' ? writable : false,
      label: typeof label === 'string' && label.trim() ? label.trim() : undefined,
      source: typeof source === 'string' && source.trim() ? source.trim() : undefined,
    });
  }
  return roots;
}
