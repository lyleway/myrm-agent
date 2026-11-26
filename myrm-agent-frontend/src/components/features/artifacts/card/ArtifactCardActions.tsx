'use client';

/**
 * [INPUT]
 * - ./IconAction, ./ArtifactAppButton, ./ArtifactLocalFileActions, ./ArtifactSharePopover (POS: row building blocks)
 * - ./artifactKinds (POS: ArtifactAbilities)
 * - ./useArtifactPublishing, ./useArtifactActions (POS: state and handlers the row drives)
 * - @/components/features/dashboard-workspace/useDashboardWorkspaceStore (POS: pin to dashboard)
 * - @/services/hosting (POS: ArtifactPublication)
 * - @/store/useChatStore (POS: current chat id for pinning)
 *
 * [OUTPUT]
 * ArtifactCardActions, PanelToggle.
 *
 * [POS]
 * The action row at the right of an artifact card. Which buttons appear depends only on
 * the artifact kind, its local path and its publishing state.
 */

import { useTranslations } from 'next-intl';
import {
  BookOpen,
  ChevronDown,
  ChevronUp,
  Copy,
  Download,
  ExternalLink,
  Eye,
  FolderOpen,
  Globe,
  MessageSquarePlus,
  Pin,
  Send,
} from 'lucide-react';
import { useDashboardWorkspaceStore } from '@/components/features/dashboard-workspace/useDashboardWorkspaceStore';
import { cn } from '@/lib/utils/classnameUtils';
import { REVEAL_ON_HOVER } from '@/lib/utils/a11y';
import type { ArtifactPublication } from '@/services/hosting';
import type { Artifact } from '@/store/chat/types';
import useChatStore from '@/store/useChatStore';
import { deploymentHostname, isSharePreviewableArtifact } from '../artifactUtils';
import { ArtifactAppButton } from './ArtifactAppButton';
import { ArtifactLocalFileActions } from './ArtifactLocalFileActions';
import { ArtifactSharePopover } from './ArtifactSharePopover';
import type { ArtifactAbilities } from './artifactKinds';
import { IconAction } from './IconAction';
import type { ArtifactActions } from './useArtifactActions';
import type { ArtifactPublishing } from './useArtifactPublishing';

const OPEN_PANEL_TONE = 'text-primary';

/** A panel the card can open below its header; absent when the artifact has no such panel. */
export interface PanelToggle {
  open: boolean;
  toggle: () => void;
}

interface ArtifactCardActionsProps {
  artifact: Artifact;
  abilities: ArtifactAbilities;
  publishing: ArtifactPublishing;
  actions: ArtifactActions;
  inlineExpanded: boolean;
  onToggleInline: () => void;
  wechat: PanelToggle | null;
  organize: PanelToggle | null;
  onPreview?: (artifact: Artifact) => void;
}

const isLive = (publication: ArtifactPublication): boolean =>
  publication.publication_status === 'READY' && Boolean(publication.publication_url);

export function ArtifactCardActions({
  artifact,
  abilities,
  publishing,
  actions,
  inlineExpanded,
  onToggleInline,
  wechat,
  organize,
  onPreview,
}: ArtifactCardActionsProps) {
  const t = useTranslations('artifacts');
  const tWidget = useTranslations('dashboardWorkspace');
  const pinned = useDashboardWorkspaceStore((state) => state.widgets.some((widget) => widget.id === artifact.id));
  const togglePinWidget = useDashboardWorkspaceStore((state) => state.togglePinWidget);
  const chatId = useChatStore((state) => state.chatId);

  const { current, canDeploy, deployPreflight } = publishing;
  const liveUrl = canDeploy ? publishing.publications.find(isLive)?.publication_url : undefined;
  const preview = () => onPreview?.(current);

  return (
    <div className={cn('flex items-center gap-1 transition-opacity duration-200', REVEAL_ON_HOVER)}>
      {artifact.file_path ? <ArtifactLocalFileActions artifactId={artifact.id} filePath={artifact.file_path} /> : null}
      {liveUrl ? (
        <IconAction
          icon={ExternalLink}
          title={t('deploy.deployedLabel', { hostname: deploymentHostname(liveUrl) })}
          toneClass="text-green-600 dark:text-green-500"
          onAction={() => window.open(liveUrl, '_blank')}
        />
      ) : null}
      {isSharePreviewableArtifact(current) ? (
        <ArtifactSharePopover artifactId={current.id} artifactType={current.type} />
      ) : null}
      {artifact.type === 'html' ? <ArtifactAppButton artifactId={current.id} name={current.filename} /> : null}
      {publishing.isDeployCandidate ? (
        <IconAction
          icon={Globe}
          title={
            canDeploy ? t('publish.openModal') : (deployPreflight?.hint ?? deployPreflight?.message ?? t('publish.openModal'))
          }
          toneClass={canDeploy ? 'text-primary' : 'text-muted-foreground opacity-60'}
          onAction={publishing.openDeploy}
        />
      ) : null}
      {wechat ? (
        <IconAction
          icon={Send}
          title={t('wechatDraft.openPanel')}
          toneClass={wechat.open ? OPEN_PANEL_TONE : undefined}
          onAction={wechat.toggle}
          testId="wechat-draft-open-panel"
        />
      ) : null}
      {organize ? (
        <IconAction
          icon={FolderOpen}
          title={t('organizePlan.openPanel')}
          toneClass={organize.open ? OPEN_PANEL_TONE : undefined}
          onAction={organize.toggle}
        />
      ) : null}
      {abilities.ingest ? (
        <IconAction
          icon={BookOpen}
          title={t('ingestToWiki.title')}
          disabled={actions.ingestLoading}
          iconClassName={actions.ingestLoading ? 'opacity-50 animate-pulse' : undefined}
          onAction={() => void actions.ingestToWiki()}
        />
      ) : null}
      <IconAction icon={MessageSquarePlus} title={t('insertToChat.title')} onAction={actions.insertToChat} />
      <IconAction
        icon={Pin}
        title={pinned ? tWidget('unpin') : tWidget('pinTooltip')}
        toneClass={pinned ? 'text-blue-600 dark:text-blue-400' : undefined}
        iconClassName={pinned ? 'fill-current' : undefined}
        testId={`artifact-pin-btn-${artifact.id}`}
        onAction={() =>
          togglePinWidget({
            id: artifact.id,
            title: current.filename,
            language: current.type,
            content: current.content || current.filename,
            sourceChatId: chatId ?? undefined,
          })
        }
      />
      {abilities.inlinePreview ? (
        <IconAction
          icon={inlineExpanded ? ChevronUp : ChevronDown}
          title={inlineExpanded ? t('inlinePreview.collapse') : t('inlinePreview.expand')}
          onAction={onToggleInline}
        />
      ) : null}
      {abilities.preview ? <IconAction icon={Eye} title={t('preview')} onAction={preview} /> : null}
      {abilities.copy ? (
        <IconAction
          icon={Copy}
          title={actions.copied ? t('copied') : t('copyCode')}
          iconClassName={actions.copied ? 'text-green-500' : undefined}
          onAction={() => void actions.copy()}
        />
      ) : null}
      <IconAction icon={ExternalLink} title={t('openInNewTab')} onAction={actions.openInNewTab} />
      <IconAction icon={Download} title={t('download')} onAction={() => void actions.download()} />
    </div>
  );
}
