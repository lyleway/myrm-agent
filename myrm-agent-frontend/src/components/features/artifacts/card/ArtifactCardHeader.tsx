'use client';

/**
 * [INPUT]
 * - ./artifactKinds (POS: getArtifactColor)
 * - ../artifactUtils (POS: getArtifactIcon, formatBytes)
 * - @/store/chat/types (POS: Artifact)
 *
 * [OUTPUT]
 * ArtifactCardHeader.
 *
 * [POS]
 * Left part of an artifact card: kind icon, file name, kind and size, and the local path
 * when the file lives on this machine.
 */

import { createElement } from 'react';
import { useTranslations } from 'next-intl';
import { cn } from '@/lib/utils/classnameUtils';
import type { Artifact } from '@/store/chat/types';
import { formatBytes, getArtifactIcon } from '../artifactUtils';
import { getArtifactColor } from './artifactKinds';

interface ArtifactCardHeaderProps {
  /** The artifact as given: it carries the local file path. */
  artifact: Artifact;
  /** The artifact as last seen by the server. */
  current: Artifact;
}

const META_TEXT = 'text-xs text-gray-500 dark:text-gray-400';
const META_DOT = <span className="text-xs text-gray-400 dark:text-gray-500">·</span>;

export function ArtifactCardHeader({ artifact, current }: ArtifactCardHeaderProps) {
  const t = useTranslations('artifacts');

  return (
    <>
      <div
        className={cn(
          'flex-shrink-0 w-10 h-10 rounded-lg flex items-center justify-center',
          getArtifactColor(artifact.type),
        )}
      >
        {createElement(getArtifactIcon(artifact.type, artifact.filename), { className: 'w-5 h-5' })}
      </div>

      <div className="flex-1 min-w-0">
        <h4 className="text-sm font-medium text-gray-900 dark:text-gray-100 truncate">{current.filename}</h4>
        <div className="flex items-center gap-2 mt-0.5 flex-wrap">
          <span className={META_TEXT}>{t(`types.${current.type}`)}</span>
          {META_DOT}
          <span className={META_TEXT}>{formatBytes(current.size)}</span>
          {current.filename.endsWith('.skill') && (
            <>
              {META_DOT}
              <span className={META_TEXT}>{t('skillActions.skillPackageHint')}</span>
            </>
          )}
        </div>
        {artifact.file_path ? (
          <p
            className="text-[10px] text-gray-400 dark:text-gray-500 truncate mt-0.5 max-w-[200px]"
            title={artifact.file_path}
          >
            {artifact.file_path}
          </p>
        ) : null}
      </div>
    </>
  );
}
