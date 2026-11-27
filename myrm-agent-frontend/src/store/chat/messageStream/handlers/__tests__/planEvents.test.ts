/**
 * A plan snapshot on the stream makes the open chat's plan panel re-read the plan.
 *
 * Real scenario: the model ticks a step off in chat A. If the user is watching chat A the panel must update while the
 * turn runs; if the user reads chat B, B's plan stays as it is; with the goals feature off there is no plan endpoint
 * to call. The event goes through the real parser and the real stream entry, so a type the parser drops or a handler
 * the registry never reaches fails here.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { usePlanStore } from '@/store/chat/goals/usePlanStore';
import { parseSseEnvelope } from '@/store/chat/schema';
import useChatStore from '@/store/useChatStore';
import { useFeatureGateStore } from '@/store/useFeatureGateStore';
import { handleMessageStream } from '../../handleMessageStream';
import { planEvents } from '../planEvents';

const OPEN_CHAT = 'chat-open';
const BACKGROUND_CHAT = 'chat-background';

const SNAPSHOT = { goal: 'Migrate', revision: 2, todos: [{ id: '1', content: 'Read code', status: 'completed' }] };

const streamOf = (chatId: string) =>
  ({
    chatId,
    messages: [{ messageId: 'msg-1', chatId, role: 'assistant', content: '', createdAt: new Date() }],
    messageAppeared: false,
    loading: true,
    scheduler: {},
  }) as never;

async function receivePlanUpdate(chatId: string): Promise<void> {
  const event = parseSseEnvelope({ type: 'plan_update', messageId: 'msg-1', data: SNAPSHOT });
  expect(event).not.toBeNull();
  await handleMessageStream(event as never, '', undefined, false, '', streamOf(chatId), {} as never);
  await vi.dynamicImportSettled();
}

describe('plan_update stream event', () => {
  const fetchPlan = vi.fn<(chatId: string) => Promise<void>>(() => Promise.resolve());

  beforeEach(() => {
    fetchPlan.mockClear();
    usePlanStore.setState({ plan: null, isLoading: false, fetchPlan });
    useChatStore.setState({ chatId: OPEN_CHAT });
    useFeatureGateStore.setState({ enabledFeatures: new Set(['goals_system']) });
  });

  it('makes the plan panel of the open chat re-read the plan', async () => {
    await receivePlanUpdate(OPEN_CHAT);

    expect(fetchPlan).toHaveBeenCalledTimes(1);
    expect(fetchPlan).toHaveBeenCalledWith(OPEN_CHAT);
  });

  it('leaves the plan of the open chat alone when a background chat reports a snapshot', async () => {
    await receivePlanUpdate(BACKGROUND_CHAT);

    expect(fetchPlan).not.toHaveBeenCalled();
  });

  it('does not call the plan endpoint while the goals feature is off', async () => {
    useFeatureGateStore.setState({ enabledFeatures: new Set() });

    await receivePlanUpdate(OPEN_CHAT);

    expect(fetchPlan).not.toHaveBeenCalled();
  });

  it('treats the snapshot as a signal: the plan shown is the one the server serves', async () => {
    await receivePlanUpdate(OPEN_CHAT);

    expect(usePlanStore.getState().plan).toBeNull();
  });

  it('lets every other event through to the next handler', async () => {
    const ctx = { data: { type: 'tasks_steps' }, state: streamOf(OPEN_CHAT) } as never;

    await expect(planEvents(ctx)).resolves.toBeNull();
    expect(fetchPlan).not.toHaveBeenCalled();
  });
});
