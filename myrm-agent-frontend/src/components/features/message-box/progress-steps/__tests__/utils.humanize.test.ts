import { describe, expect, it } from 'vitest';

import type { ProgressItem } from '@/store/chat/types/progress';
import { getStepTitle } from '../utils';

const progressT = (key: string) => {
  if (key === 'file_write_tool') {
    return 'Writing file';
  }
  return key;
};

const humanizeT = (key: string, values?: Record<string, string | number>) => {
  const dict: Record<string, string> = {
    'progress.file_write': 'Wrote {filename}',
    'progress.mcp': 'Use {server} — {tool}',
  };
  let out = dict[key] ?? key;
  if (values) {
    for (const [k, v] of Object.entries(values)) {
      out = out.replace(`{${k}}`, String(v));
    }
  }
  return out;
};

const serverName = (identity: string) => (identity === 'unnamed_dd4acd' ? '天气' : undefined);

describe('getStepTitle humanize integration', () => {
  it('prefers humanize one-liner over raw tool name', () => {
    const step = {
      step_key: 'file_write_tool',
      tool_name: 'file_write_tool',
      items: [{ file_path: '/workspace/report.md' }],
    } as ProgressItem;
    expect(getStepTitle(step, progressT, true, humanizeT)).toBe('Wrote report.md');
  });

  it('names an MCP server the way its owner named it, not by the id in the tool name', () => {
    const tool = 'mcp__unnamed_dd4acd__get_forecast';
    const step = { step_key: tool, tool_name: tool } as ProgressItem;

    expect(getStepTitle(step, progressT, true, humanizeT, serverName)).toBe('Use 天气 — get forecast');
  });

  it('humanizes an MCP tool name of up to the 64 characters its budget allows', () => {
    const prefix = 'mcp__unnamed_dd4acd__';
    const longest = `${prefix}${'x'.repeat(64 - prefix.length)}`;
    const step = { step_key: longest, tool_name: longest } as ProgressItem;

    expect(longest).toHaveLength(64);
    expect(getStepTitle(step, progressT, true, humanizeT, serverName)).toBe(
      `Use 天气 — ${'x'.repeat(64 - prefix.length)}`,
    );
  });

  it('does not take a text longer than any tool name for a tool', () => {
    const notAName = `mcp__unnamed_dd4acd__${'x'.repeat(60)}`;
    const step = { step_key: 'alpha', tool_name: notAName } as ProgressItem;

    expect(getStepTitle(step, progressT, true, humanizeT, serverName)).not.toContain('天气');
  });
});
