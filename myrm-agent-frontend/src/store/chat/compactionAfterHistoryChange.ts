/**
 * [INPUT]
 * @/store/useChatStore (POS: open-chat state; its `refreshCompactionState` absorbs the server's summary)
 *
 * [OUTPUT]
 * refreshCompactionAfterHistoryChange: Re-read the compaction summary after the server rewrote the stored history.
 *
 * [POS]
 * A rewind, an edit-resend or a regenerate trims the transcript in the store but not the summary card, and the server
 * drops the summary in the same change when the history it summarized was rewritten. Only a chat that shows a summary
 * can show a stale one, so a chat without a summary costs no request.
 */

import useChatStore from '@/store/useChatStore';

export function refreshCompactionAfterHistoryChange(chatId: string): void {
  const state = useChatStore.getState();
  if (state.chatId !== chatId || !state.compactedSummary) {
    return;
  }
  void state.refreshCompactionState(chatId);
}
