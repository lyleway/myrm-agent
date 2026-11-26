'use client';

/**
 * [INPUT]
 * - @/lib/api::apiRequest (POS: unified API request entry)
 * - @/lib/utils/clipboardUtils::writeToClipboard (POS: clipboard write)
 *
 * [OUTPUT]
 * - ArtifactLocalFileActions: card actions for an artifact that lives on disk: open with the default app, reveal in the
 *   file manager, copy the path.
 *
 * [POS]
 * Local-file controls of an artifact card, rendered as sibling buttons of the card's action row.
 */

import { useCallback, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Copy, FolderOpen, Play } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/primitives/button';
import { apiRequest } from '@/lib/api';
import { cn } from '@/lib/utils/classnameUtils';
import { writeToClipboard } from '@/lib/utils/clipboardUtils';

interface ArtifactLocalFileActionsProps {
  artifactId: string;
  filePath: string;
}

const ACTION_CLASS = 'h-8 w-8 text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200';

export function ArtifactLocalFileActions({ artifactId, filePath }: ArtifactLocalFileActionsProps) {
  const t = useTranslations('artifacts');
  const [pathCopied, setPathCopied] = useState(false);

  const handleRevealInFileManager = useCallback(async () => {
    try {
      await apiRequest(`/files/${artifactId}/reveal`, { method: 'POST' });
    } catch {
      toast.error(t('errors.serverError'));
    }
  }, [artifactId, t]);

  const handleOpenWithDefaultApp = useCallback(async () => {
    try {
      await apiRequest(`/files/${artifactId}/open`, { method: 'POST' });
    } catch {
      toast.error(t('errors.serverError'));
    }
  }, [artifactId, t]);

  const handleCopyPath = useCallback(async () => {
    await writeToClipboard(filePath);
    setPathCopied(true);
    setTimeout(() => setPathCopied(false), 2000);
  }, [filePath]);

  return (
    <>
      <Button
        variant="ghost"
        size="icon"
        className={ACTION_CLASS}
        onClick={(e) => {
          e.stopPropagation();
          handleOpenWithDefaultApp();
        }}
        title={t('openWithDefaultApp')}
      >
        <Play className="w-4 h-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={ACTION_CLASS}
        onClick={(e) => {
          e.stopPropagation();
          handleRevealInFileManager();
        }}
        title={t('revealInFileManager')}
      >
        <FolderOpen className="w-4 h-4" />
      </Button>
      <Button
        variant="ghost"
        size="icon"
        className={ACTION_CLASS}
        onClick={(e) => {
          e.stopPropagation();
          handleCopyPath();
        }}
        title={pathCopied ? t('copied') : t('copyPath')}
      >
        <Copy className={cn('w-3.5 h-3.5', pathCopied && 'text-green-500')} />
      </Button>
    </>
  );
}
