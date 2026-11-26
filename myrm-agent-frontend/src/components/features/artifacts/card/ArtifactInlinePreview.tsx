'use client';

/**
 * [INPUT]
 * - ../renderers/MediaPreview, ../renderers/MermaidPreview (POS: visual renderers)
 * - ../artifactUtils (POS: formatBytes)
 * - @/components/primitives/button (POS: Button)
 * - @/store/chat/types (POS: Artifact)
 *
 * [OUTPUT]
 * ArtifactInlinePreview.
 *
 * [POS]
 * The expandable visual preview under an artifact card. It shows one of four states: the
 * oversized-file hint, a spinner while loading, the rendered content, or a load error.
 */

import { useTranslations } from 'next-intl';
import { Eye } from 'lucide-react';
import { Button } from '@/components/primitives/button';
import type { Artifact } from '@/store/chat/types';
import { formatBytes } from '../artifactUtils';
import { HtmlPreview, SvgPreview } from '../renderers/MediaPreview';
import MermaidPreview from '../renderers/MermaidPreview';

interface ArtifactInlinePreviewProps {
  artifact: Artifact;
  isLargeFile: boolean;
  loading: boolean;
  content: string | null;
  onOpenFullscreen: () => void;
}

export function ArtifactInlinePreview({
  artifact,
  isLargeFile,
  loading,
  content,
  onOpenFullscreen,
}: ArtifactInlinePreviewProps) {
  const t = useTranslations('artifacts');

  return (
    <div className="border-t border-gray-200/60 dark:border-gray-700/60">
      {isLargeFile ? (
        <div className="flex flex-col items-center justify-center gap-2 py-6">
          <span className="text-sm text-muted-foreground">
            {t('inlinePreview.largeFileHint', { size: formatBytes(artifact.size) })}
          </span>
          <Button
            variant="outline"
            size="sm"
            className="h-7 text-xs"
            onClick={(event) => {
              event.stopPropagation();
              onOpenFullscreen();
            }}
          >
            <Eye className="w-3.5 h-3.5 mr-1.5" />
            {t('inlinePreview.viewFullscreen')}
          </Button>
        </div>
      ) : loading && !content ? (
        <div className="flex items-center justify-center py-8">
          <div className="animate-spin w-5 h-5 border-2 border-muted-foreground/30 border-t-primary rounded-full" />
        </div>
      ) : content ? (
        <div className="min-h-[100px]">
          {artifact.type === 'svg' ? (
            <SvgPreview content={content} />
          ) : artifact.type === 'mermaid' ? (
            <MermaidPreview content={content} />
          ) : (
            <HtmlPreview content={content} autoHeight injectTheme />
          )}
        </div>
      ) : (
        <div className="flex items-center justify-center py-6 text-sm text-muted-foreground">
          {t('inlinePreview.loadError')}
        </div>
      )}
    </div>
  );
}
