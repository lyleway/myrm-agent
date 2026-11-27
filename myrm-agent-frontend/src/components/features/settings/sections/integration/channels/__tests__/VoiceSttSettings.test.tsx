/** @vitest-environment jsdom */
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';

import { VoiceSttSettings } from '../VoiceSttSettings';
import { DEFAULT_VOICE_FORM, type VoiceFormState } from '../voiceFormState';

const stableT = (key: string) => key;
vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
}));

function renderSettings(overrides: Partial<VoiceFormState>, isLocalStt = false) {
  const update = vi.fn();
  render(
    <VoiceSttSettings
      form={{ ...DEFAULT_VOICE_FORM, sttEnabled: true, ...overrides }}
      update={update}
      isLocalStt={isLocalStt}
      needsSttApiKey={!isLocalStt}
      localSttAvailable
    />,
  );
  return update;
}

describe('VoiceSttSettings model field', () => {
  afterEach(cleanup);

  it.each(['openai', 'groq', 'deepgram'])('starts empty for %s, so the provider picks its own model', (sttProvider) => {
    renderSettings({ sttProvider });

    const field = screen.getByLabelText('sttModel');

    expect(field).toHaveValue('');
    expect(field).toHaveAttribute('placeholder', 'sttModelPlaceholder');
  });

  it('passes a typed model on as it was typed', () => {
    const update = renderSettings({ sttProvider: 'groq' });

    fireEvent.change(screen.getByLabelText('sttModel'), { target: { value: 'whisper-large-v3-turbo' } });

    expect(update).toHaveBeenCalledWith({ sttModel: 'whisper-large-v3-turbo' });
  });

  it('is left out for the local engine, which has its own model sizes', () => {
    renderSettings({ sttProvider: 'local' }, true);

    expect(screen.queryByLabelText('sttModel')).not.toBeInTheDocument();
    expect(screen.getByLabelText('sttLocalModel')).toBeInTheDocument();
  });
});
