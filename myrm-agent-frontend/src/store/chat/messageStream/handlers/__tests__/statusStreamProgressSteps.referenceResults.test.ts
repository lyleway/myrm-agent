/**
 * A consensus or MoA run reports each reference model as it finishes. The reducer keeps those results on the message in
 * the order they arrive, so the panel can show who answered and how long it took; a field that is missing or of the
 * wrong type reads as empty instead of failing the stream.
 */
import { describe, expect, it } from 'vitest';

import { applyStatusEvent, statusStream } from './statusStepTestKit';

describe('consensus_reference_done', () => {
  it('words the step with the model, how it ended and how long it took', async () => {
    const message = await applyStatusEvent('consensus_reference_done', {
      model: 'model-a',
      success: true,
      elapsed: 1.26,
    });

    expect(message.progressSteps?.[0].items).toEqual([{ text: 'model-a (\u2713 1.3s)' }]);
  });

  it('keeps every result on the message in the order the models finish', async () => {
    const stream = statusStream();

    await stream.apply('consensus_reference_done', {
      model: 'model-a',
      success: true,
      elapsed: 1.2,
      content: 'answer a',
    });
    await stream.apply('consensus_reference_done', { model: 'model-b', success: false, elapsed: 3.4 });

    expect(stream.message.consensusRefs).toEqual([
      { model: 'model-a', success: true, elapsed: 1.2, content: 'answer a' },
      { model: 'model-b', success: false, elapsed: 3.4 },
    ]);
  });

  it('reads a missing model, outcome or time as empty and a non-text answer as none', async () => {
    const message = await applyStatusEvent('consensus_reference_done', { elapsed: 'slow', content: 42 });

    expect(message.consensusRefs).toEqual([{ model: '', success: false, elapsed: 0 }]);
    expect(message.progressSteps?.[0].items).toEqual([]);
  });
});

describe('moa_ref_done', () => {
  it('keeps the result on the message, marked as coming from the overlay', async () => {
    const message = await applyStatusEvent('moa_ref_done', {
      model: 'model-a',
      success: true,
      elapsed: 2.5,
      content: 'draft',
    });

    expect(message.consensusRefs).toEqual([
      { model: 'model-a', success: true, elapsed: 2.5, content: 'draft', source: 'moa_overlay' },
    ]);
  });

  it('reads a missing model, outcome or time as empty and a non-text answer as none', async () => {
    const message = await applyStatusEvent('moa_ref_done', { elapsed: null, content: {} });

    expect(message.consensusRefs).toEqual([{ model: '', success: false, elapsed: 0, source: 'moa_overlay' }]);
  });
});
