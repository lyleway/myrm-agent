import { beforeEach, describe, expect, it } from 'vitest';
import { DEFAULT_ENABLED_BUILTIN_TOOLS } from '@/store/chat/types';
import useChatStore, { hydrateChatPreferencesFromStorage } from '@/store/useChatStore';

const STORAGE_KEY = 'currentBuiltinTools';
// Retired from the product: the server rejects it in any Agent-mode request.
const RETIRED_TOOL_ID = 'answer_tool';

function hydrateWithStored(value: unknown): string[] {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  hydrateChatPreferencesFromStorage();
  return useChatStore.getState().currentBuiltinTools;
}

describe('useChatStore - stored built-in tools', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('restores the stored tools in their stored order', () => {
    expect(hydrateWithStored(['browser', 'web_search', 'cron'])).toEqual(['browser', 'web_search', 'cron']);
  });

  it('drops a retired tool ID so no request carries it', () => {
    expect(hydrateWithStored(['web_search', RETIRED_TOOL_ID, 'memory', 'browser'])).toEqual([
      'web_search',
      'memory',
      'browser',
    ]);
  });

  it('keeps the explicit choice when a retired ID is the only thing that differs from the former default pair', () => {
    expect(hydrateWithStored(['web_search', 'memory', RETIRED_TOOL_ID])).toEqual(['web_search', 'memory']);
  });

  it('upgrades the former default pair to the current default', () => {
    expect(hydrateWithStored(['web_search', 'memory'])).toEqual([...DEFAULT_ENABLED_BUILTIN_TOOLS]);
  });

  it('falls back to the default when the stored value is unreadable', () => {
    localStorage.setItem(STORAGE_KEY, '{not json');
    hydrateChatPreferencesFromStorage();
    expect(useChatStore.getState().currentBuiltinTools).toEqual([...DEFAULT_ENABLED_BUILTIN_TOOLS]);
  });
});
