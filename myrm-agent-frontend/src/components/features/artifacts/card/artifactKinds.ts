/**
 * [INPUT]
 * - @/store/chat/types (POS: ArtifactType union)
 *
 * [OUTPUT]
 * getArtifactColor, abilitiesOf, ArtifactAbilities.
 *
 * [POS]
 * What the artifact card offers for each artifact kind (preview, copy, inline preview, ingest,
 * install) and the kind's color, one lookup per kind. Other per-kind rules live elsewhere, for
 * example share and deploy eligibility (`../artifactUtils`), hover preloading
 * (`./useArtifactContent`) and the WeChat draft panel (`../ArtifactCard`).
 */

import type { ArtifactType } from '@/store/chat/types';

const KIND_COLORS: Partial<Record<ArtifactType, string>> = {
  code: 'text-emerald-600 dark:text-emerald-400 bg-emerald-50 dark:bg-emerald-900/20',
  document: 'text-blue-600 dark:text-blue-400 bg-blue-50 dark:bg-blue-900/20',
  html: 'text-orange-600 dark:text-orange-400 bg-orange-50 dark:bg-orange-900/20',
  pdf: 'text-red-600 dark:text-red-400 bg-red-50 dark:bg-red-900/20',
  image: 'text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20',
  svg: 'text-purple-600 dark:text-purple-400 bg-purple-50 dark:bg-purple-900/20',
  mermaid: 'text-cyan-600 dark:text-cyan-400 bg-cyan-50 dark:bg-cyan-900/20',
  spreadsheet: 'text-green-600 dark:text-green-400 bg-green-50 dark:bg-green-900/20',
  presentation: 'text-amber-600 dark:text-amber-400 bg-amber-50 dark:bg-amber-900/20',
  word_document: 'text-sky-600 dark:text-sky-400 bg-sky-50 dark:bg-sky-900/20',
};

const FALLBACK_COLOR = 'text-gray-600 dark:text-gray-400 bg-gray-50 dark:bg-gray-900/20';

const PREVIEWABLE: ReadonlySet<ArtifactType> = new Set<ArtifactType>([
  'code',
  'document',
  'html',
  'image',
  'svg',
  'mermaid',
  'pdf',
  'spreadsheet',
  'presentation',
  'word_document',
]);
const COPYABLE: ReadonlySet<ArtifactType> = new Set<ArtifactType>([
  'code',
  'document',
  'html',
  'svg',
  'mermaid',
  'spreadsheet',
]);
const INLINE_PREVIEWABLE: ReadonlySet<ArtifactType> = new Set<ArtifactType>(['html', 'svg', 'mermaid']);
const INGESTABLE: ReadonlySet<ArtifactType> = new Set<ArtifactType>(['code', 'document', 'html', 'svg', 'mermaid']);
const INSTALLABLE: ReadonlySet<ArtifactType> = new Set<ArtifactType>(['html']);

export interface ArtifactAbilities {
  preview: boolean;
  copy: boolean;
  inlinePreview: boolean;
  ingest: boolean;
  install: boolean;
}

export function getArtifactColor(type: ArtifactType): string {
  return KIND_COLORS[type] ?? FALLBACK_COLOR;
}

export function abilitiesOf(type: ArtifactType): ArtifactAbilities {
  return {
    preview: PREVIEWABLE.has(type),
    copy: COPYABLE.has(type),
    inlinePreview: INLINE_PREVIEWABLE.has(type),
    ingest: INGESTABLE.has(type),
    install: INSTALLABLE.has(type),
  };
}
