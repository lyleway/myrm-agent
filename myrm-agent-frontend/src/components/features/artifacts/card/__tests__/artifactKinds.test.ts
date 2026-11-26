/**
 * [INPUT]
 * - ../artifactKinds::abilitiesOf
 * - @/store/chat/types::ARTIFACT_TYPES (POS: every artifact kind)
 *
 * [OUTPUT]
 * - Tests: which abilities each artifact kind gets, and that only an HTML page can be installed as an app.
 *
 * [POS]
 * The per-kind table the card's action row reads; a kind added to the union gets no install button until it is listed.
 */

import { describe, expect, it } from 'vitest';
import { ARTIFACT_TYPES } from '@/store/chat/types';
import { abilitiesOf } from '../artifactKinds';

describe('abilitiesOf', () => {
  it.each(ARTIFACT_TYPES)('%s can be installed as an app only when it is an HTML page', (type) => {
    expect(abilitiesOf(type).install).toBe(type === 'html');
  });

  it('offers every ability for an HTML page', () => {
    expect(abilitiesOf('html')).toEqual({
      preview: true,
      copy: true,
      inlinePreview: true,
      ingest: true,
      install: true,
    });
  });

  it('offers a PDF a preview and nothing else', () => {
    expect(abilitiesOf('pdf')).toEqual({
      preview: true,
      copy: false,
      inlinePreview: false,
      ingest: false,
      install: false,
    });
  });
});
