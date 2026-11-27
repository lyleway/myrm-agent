/**
 * A rewind, an edit-resend or a regenerate can make the server drop the compaction summary the open chat shows.
 *
 * Real scenario: the user compacts a long chat, then rewinds to an earlier message. The server dropped the summary of
 * the removed turns; the summary card must not stay on screen, while a chat that never showed one costs no request.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import useChatStore from '@/store/useChatStore';
import { refreshCompactionAfterHistoryChange } from '../compactionAfterHistoryChange';

const OPEN_CHAT = 'chat-open';

describe('refreshCompactionAfterHistoryChange', () => {
  const refreshCompactionState = vi.fn(async () => undefined);

  beforeEach(() => {
    refreshCompactionState.mockClear();
    useChatStore.setState({ chatId: OPEN_CHAT, compactedSummary: 'summary of earlier turns', refreshCompactionState });
  });

  it('re-reads the summary of the open chat that shows one', () => {
    refreshCompactionAfterHistoryChange(OPEN_CHAT);

    expect(refreshCompactionState).toHaveBeenCalledTimes(1);
    expect(refreshCompactionState).toHaveBeenCalledWith(OPEN_CHAT);
  });

  it('costs no request when the open chat shows no summary', () => {
    useChatStore.setState({ compactedSummary: null });

    refreshCompactionAfterHistoryChange(OPEN_CHAT);

    expect(refreshCompactionState).not.toHaveBeenCalled();
  });

  it('leaves the open chat alone when the change was made to another chat', () => {
    refreshCompactionAfterHistoryChange('chat-other');

    expect(refreshCompactionState).not.toHaveBeenCalled();
  });
});
