/**
 * [INPUT]
 * - @/lib/api (POS: getStorageUrl)
 * - @/store/useArtifactPortalStore (POS: shared per-artifact content cache)
 * - @/store/chat/types (POS: Artifact)
 *
 * [OUTPUT]
 * useArtifactContent: fetchContent, preloadHandlers.
 *
 * [POS]
 * One cached read of an artifact's text, shared by the card's inline preview, copy and
 * the draft/plan panels, plus a hover warm-up so a click on the card opens instantly.
 */

import { useCallback, useEffect, useRef } from 'react';
import { getStorageUrl } from '@/lib/api';
import type { Artifact } from '@/store/chat/types';
import useArtifactPortalStore from '@/store/useArtifactPortalStore';

const PRELOAD_DELAY_MS = 200;
const PRELOADABLE_TYPES: ReadonlySet<string> = new Set(['code', 'document', 'svg', 'mermaid', 'html']);

interface ArtifactContent {
  /** Cached text, or a fresh fetch; null when the file cannot be read. */
  fetchContent: () => Promise<string | null>;
  preloadHandlers: { onMouseEnter: () => void; onMouseLeave: () => void };
}

export function useArtifactContent(artifact: Artifact, skipPreload: boolean): ArtifactContent {
  const { getCachedContent, setCachedContent } = useArtifactPortalStore();

  const fetchContent = useCallback(async (): Promise<string | null> => {
    const cached = getCachedContent(artifact.id);
    if (cached) {
      return cached;
    }
    try {
      const response = await fetch(getStorageUrl(artifact.preview_url));
      if (response.ok) {
        const text = await response.text();
        setCachedContent(artifact.id, text);
        return text;
      }
    } catch {
      // Callers show their own fallback when nothing could be read.
    }
    return null;
  }, [artifact.id, artifact.preview_url, getCachedContent, setCachedContent]);

  const timerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const onMouseLeave = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
  }, []);

  const onMouseEnter = useCallback(() => {
    timerRef.current = setTimeout(() => {
      if (PRELOADABLE_TYPES.has(artifact.type) && !skipPreload) {
        void fetchContent();
      }
    }, PRELOAD_DELAY_MS);
  }, [artifact.type, skipPreload, fetchContent]);

  // A card that unmounts mid-hover must not start a fetch afterwards.
  useEffect(() => onMouseLeave, [onMouseLeave]);

  return { fetchContent, preloadHandlers: { onMouseEnter, onMouseLeave } };
}
