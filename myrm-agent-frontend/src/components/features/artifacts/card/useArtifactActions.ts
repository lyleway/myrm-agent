/**
 * [INPUT]
 * - ../artifactUtils (POS: getDownloadFilename)
 * - @/lib/api (POS: getStorageUrl)
 * - @/lib/deploy-mode (POS: isTauriRuntime)
 * - @/lib/utils/clipboardUtils (POS: writeToClipboard)
 * - @/services/wikiService (POS: ingest an artifact into the wiki)
 * - @/store/useChatStore (POS: attached chat files, active agent)
 *
 * [OUTPUT]
 * useArtifactActions, ArtifactActions.
 *
 * [POS]
 * One-shot actions of the artifact card: copy, open in a new tab, save to the wiki,
 * attach to the chat composer and download.
 */

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';
import { getStorageUrl } from '@/lib/api';
import { isTauriRuntime } from '@/lib/deploy-mode';
import { writeToClipboard } from '@/lib/utils/clipboardUtils';
import { wikiService } from '@/services/wikiService';
import type { Artifact } from '@/store/chat/types';
import useChatStore from '@/store/useChatStore';
import { getDownloadFilename } from '../artifactUtils';

const COPIED_FLASH_MS = 2000;

export interface ArtifactActions {
  copied: boolean;
  ingestLoading: boolean;
  copy: () => Promise<void>;
  openInNewTab: () => void;
  ingestToWiki: () => Promise<void>;
  insertToChat: () => void;
  download: () => Promise<void>;
}

export function useArtifactActions(
  artifact: Artifact,
  current: Artifact,
  fetchContent: () => Promise<string | null>,
  onDownload?: (artifact: Artifact) => void,
): ArtifactActions {
  const t = useTranslations('artifacts');
  const [copied, setCopied] = useState(false);
  const [ingestLoading, setIngestLoading] = useState(false);

  const copy = async () => {
    try {
      const text = await fetchContent();
      if (text) {
        await writeToClipboard(text);
        setCopied(true);
        setTimeout(() => setCopied(false), COPIED_FLASH_MS);
      }
    } catch {
      // The clipboard API is unavailable outside secure contexts.
    }
  };

  const openInNewTab = () => {
    window.open(getStorageUrl(artifact.preview_url), '_blank', 'noopener,noreferrer');
  };

  const ingestToWiki = async () => {
    if (ingestLoading) {
      return;
    }
    setIngestLoading(true);
    try {
      const agentId = useChatStore.getState().agentConfig?.agentId;
      const result = await wikiService.ingestArtifact(artifact.id, agentId);
      if (result.success) {
        toast.success(t('ingestToWiki.success'), { description: result.message });
      } else {
        toast.error(t('ingestToWiki.failed'), { description: result.message });
      }
    } catch {
      toast.error(t('ingestToWiki.failed'));
    } finally {
      setIngestLoading(false);
    }
  };

  const insertToChat = () => {
    const isLocalMode = isTauriRuntime() && !!artifact.file_path;
    const chatFile = {
      fileName: artifact.filename,
      fileExtension: artifact.filename.split('.').pop() || '',
      localPath: isLocalMode ? artifact.file_path : undefined,
      fileUrl: isLocalMode ? undefined : getStorageUrl(artifact.preview_url),
      fileType: (isLocalMode ? 'local_path' : 'uploaded') as 'local_path' | 'uploaded',
    };
    const currentFiles = useChatStore.getState().files;
    const alreadyAttached = currentFiles.some(
      (file) =>
        file.fileName === chatFile.fileName &&
        ((chatFile.fileUrl && file.fileUrl === chatFile.fileUrl) ||
          (chatFile.localPath && file.localPath === chatFile.localPath)),
    );
    if (alreadyAttached) {
      toast.info(t('insertToChat.alreadyAttached'));
      return;
    }
    useChatStore.getState().setFiles([...currentFiles, chatFile]);
    toast.success(t('insertToChat.success'));
  };

  const download = async () => {
    if (onDownload) {
      onDownload(current);
      return;
    }
    try {
      const response = await fetch(current.download_url);
      const blob = await response.blob();
      const url = window.URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = url;
      link.download = getDownloadFilename(current.filename);
      document.body.appendChild(link);
      link.click();
      document.body.removeChild(link);
      window.URL.revokeObjectURL(url);
    } catch (error) {
      console.error('Download failed:', error);
    }
  };

  return { copied, ingestLoading, copy, openInNewTab, ingestToWiki, insertToChat, download };
}
