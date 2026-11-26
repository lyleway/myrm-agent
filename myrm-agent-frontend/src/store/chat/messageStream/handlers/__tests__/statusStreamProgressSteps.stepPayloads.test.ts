/**
 * Beyond its own entry, a STATUS step can put more on the message or in front of the user: the constraints a compaction
 * dropped, the level and position of a workflow notification, the state of a media analysis, the warning that content was
 * truncated. An event that is not a STATUS event is refused.
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { toast } from '@/lib/utils/toast';
import type { StreamCtx } from '../../streamContext';
import { applyStatusProgressStep } from '../statusStreamProgressSteps';
import { applyStatusEvent, statusStream } from './statusStepTestKit';

vi.mock('@/lib/utils/toast', () => ({ toast: { warning: vi.fn() } }));

describe('a compaction that dropped constraint snippets', () => {
  const snippet = 'always answer in French';

  it('lists the snippets as the details of a step that has nothing else to say', async () => {
    const message = await applyStatusEvent('context_compaction', {
      phase: 'completed',
      dropped_manifest: [snippet, '  ', 7],
    });

    expect(message.progressSteps?.[0]).toMatchObject({ dropped_manifest: [snippet], items: [{ text: snippet }] });
  });

  it('keeps the tokens saved as the details and still carries the snippets', async () => {
    const message = await applyStatusEvent('context_compaction', {
      phase: 'completed',
      tokens_saved: 900,
      dropped_manifest: [snippet],
    });

    expect(message.progressSteps?.[0]).toMatchObject({
      dropped_manifest: [snippet],
      items: [{ text: '(Tokens saved: 900)' }],
    });
  });

  it.each([{ manifest: [] }, { manifest: ['', '  ', 7] }])(
    'carries no snippets for the manifest $manifest',
    async ({ manifest }) => {
      const message = await applyStatusEvent('context_compaction', { phase: 'completed', dropped_manifest: manifest });

      expect(message.progressSteps?.[0].dropped_manifest).toBeUndefined();
      expect(message.progressSteps?.[0].items).toEqual([]);
    },
  );

  it('ignores the snippets of a compaction that did not complete', async () => {
    const message = await applyStatusEvent('context_compaction', { phase: 'ineffective', dropped_manifest: [snippet] });

    expect(message.progressSteps?.[0].dropped_manifest).toBeUndefined();
  });
});

describe('a workflow stage notification', () => {
  it.each([
    { notify_level: 'alert', status: 'error' },
    { notify_level: 'warn', status: 'warning' },
    { notify_level: 'info', status: 'in_progress' },
  ])('turns a $notify_level notification into the status $status', async ({ notify_level, status }) => {
    const message = await applyStatusEvent(
      'workflow_stage',
      { message: 'Rendering', notify_level },
      { status: 'in_progress' },
    );

    expect(message.progressSteps?.[0].status).toBe(status);
  });

  it('shows where in the workflow the stage is', async () => {
    const message = await applyStatusEvent('workflow_stage', {
      message: 'Rendering',
      notify_step_index: 2,
      notify_total_steps: 5,
    });

    expect(message.progressSteps?.[0]).toMatchObject({ reason: '2 / 5', notify_step_index: 2, notify_total_steps: 5 });
  });

  it('leaves the position out while the workflow does not say how many steps it has', async () => {
    const message = await applyStatusEvent('workflow_stage', {
      message: 'Rendering',
      notify_step_index: 2,
      notify_total_steps: 0,
    });

    expect(message.progressSteps?.[0].reason).toBeUndefined();
  });
});

describe('a media analysis step', () => {
  it('marks the message as being analysed and names the vision backend doing it', async () => {
    const message = await applyStatusEvent('analyzing_image', { vision_backend: 'backend-a' });

    expect(message).toMatchObject({ mediaAnalysisStatus: 'analyzing_image', visionBackend: 'backend-a' });
  });

  it('marks the message without a backend when the event names none', async () => {
    const message = await applyStatusEvent('analyzing_video', {});

    expect(message.mediaAnalysisStatus).toBe('analyzing_video');
    expect(message.visionBackend).toBeUndefined();
  });
});

describe('a message that has no progress steps yet', () => {
  it('gets its first step from the event', async () => {
    const stream = statusStream({ progressSteps: undefined });

    await stream.apply('transient_retry', {}, { attempt: 3 });

    expect(stream.message.progressSteps).toEqual([
      expect.objectContaining({ step_key: 'transient_retry', items: [{ text: '(3/15)' }] }),
    ]);
  });
});

describe('content truncated to fit the context', () => {
  beforeEach(() => {
    vi.mocked(toast.warning).mockClear();
  });

  it('warns the user with the message the server sends', async () => {
    await applyStatusEvent('ux_warning_truncated', { message: 'Cut 3 large files' });

    expect(toast.warning).toHaveBeenCalledWith('Cut 3 large files', { duration: 8000 });
  });

  it('warns in general terms when the server sends none', async () => {
    await applyStatusEvent('ux_warning_truncated', {});

    expect(toast.warning).toHaveBeenCalledWith(expect.stringContaining('truncated'), { duration: 8000 });
  });
});

describe('an event that is not a STATUS event', () => {
  it('is refused', async () => {
    const ctx = { data: { type: 'message', messageId: 'msg-1' } } as unknown as StreamCtx;

    await expect(applyStatusProgressStep(ctx, 'context_compaction')).rejects.toThrow(
      'Expected status SSE event, received message',
    );
  });
});
