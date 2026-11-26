/**
 * [INPUT]
 * - ../statusStreamProgressSteps::applyStatusProgressStep (POS: STATUS reducer that turns a step_key into a progress step)
 * - ../../streamContext::StreamCtx (POS: Shared context for messageStream/handlers/* event slices)
 * - @/store/chat/types::Message (POS: Assistant message a progress step is written onto)
 *
 * [OUTPUT]
 * - statusStream(): an assistant message and the means to feed it STATUS events one after another
 * - applyStatusEvent(): feeds one STATUS event to a fresh message and returns it
 *
 * [POS]
 * Shared harness of the STATUS progress step tests that run the real reducer and read the message back. The `vi.mock`
 * calls stay in each test file, where vitest hoists them.
 */
import { vi } from 'vitest';

import type { Message } from '@/store/chat/types';
import type { StreamCtx } from '../../streamContext';
import { applyStatusProgressStep } from '../statusStreamProgressSteps';

const MESSAGE_ID = 'msg-1';
const CHAT_ID = 'chat-1';

/** The fields of the STATUS event itself, beside its `data` payload. */
type StatusEnvelope = Record<string, unknown>;

/**
 * An assistant message that takes STATUS events as it does during a stream. `message` is the live message the reducer
 * writes onto, so a test reads it after `apply`; `overrides` sets what the message starts with.
 */
export function statusStream(overrides: Partial<Message> = {}) {
  const state: { messages: Message[] } = {
    messages: [
      {
        content: '',
        messageId: MESSAGE_ID,
        chatId: CHAT_ID,
        role: 'assistant',
        progressSteps: [],
        createdAt: new Date(),
        ...overrides,
      },
    ],
  };
  return {
    message: state.messages[0],
    async apply(stepKey: string, payload: Record<string, unknown>, envelope: StatusEnvelope = {}): Promise<void> {
      const ctx: StreamCtx = {
        data: { type: 'status', messageId: MESSAGE_ID, step_key: stepKey, ...envelope, data: payload } as never,
        input: '',
        sources: undefined,
        added: true,
        recievedMessage: '',
        state: { chatId: CHAT_ID, messages: state.messages } as never,
        actions: {
          setLoading: vi.fn(),
          setMessages: vi.fn((updater: (s: typeof state) => void) => updater(state)),
        } as never,
        files: [],
      };
      await applyStatusProgressStep(ctx, stepKey);
    },
  };
}

/** Feed one STATUS event to a fresh message and return that message. */
export async function applyStatusEvent(
  stepKey: string,
  payload: Record<string, unknown>,
  envelope: StatusEnvelope = {},
): Promise<Message> {
  const stream = statusStream();
  await stream.apply(stepKey, payload, envelope);
  return stream.message;
}
