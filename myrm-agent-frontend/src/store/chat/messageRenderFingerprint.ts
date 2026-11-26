import type { Message } from '@/store/chat/types';

/** Summary string of a message list: the id, content length and source count of every message, in order. */
export function buildMessageRenderFingerprint(messages: Pick<Message, 'messageId' | 'content' | 'sources'>[]): string {
  return messages.map((m) => `${m.messageId}:${m.content?.length || 0}:${m.sources?.length || 0}`).join('|');
}
