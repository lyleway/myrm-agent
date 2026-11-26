/**
 * [POS]
 * Chat SSE event handler slice (planEvents).
 *
 * A plan_update snapshot tells the plan panel that the plan changed; the panel re-reads the plan the server serves.
 */

import type { StreamCtx, StreamTurn } from '../streamContext';
import { done } from '../streamContext';
import * as H from './handlerDeps';

export async function planEvents(ctx: StreamCtx): Promise<StreamTurn | null> {
  if (ctx.data.type !== H.AgentEventType.PLAN_UPDATE) {
    return null;
  }
  H.refreshPlanForOpenChat(ctx.state);
  return done(ctx);
}
