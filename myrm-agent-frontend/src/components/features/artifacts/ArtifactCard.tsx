'use client';

/**
 * [INPUT]
 * - ./card/* (POS: card header, action row, inline preview, panels, publication banners and their hooks)
 * - ./PublishModal (POS: publish dialog)
 * - ./organizePlanUtils (POS: isOrganizePlanArtifact)
 * - @/lib/constants/artifact (POS: LARGE_FILE_THRESHOLD)
 *
 * [OUTPUT]
 * ArtifactCard (default).
 *
 * [POS]
 * Card for one artifact in a chat message. Composes the card parts and holds the state that spans them:
 * which sections are open, the inline preview text and the single click-to-preview target. It also decides
 * which optional panels the artifact has.
 */

import React, { useCallback, useState } from 'react';
import { LARGE_FILE_THRESHOLD } from '@/lib/constants/artifact';
import { cn } from '@/lib/utils/classnameUtils';
import { activateOnKey } from '@/lib/utils/a11y';
import type { Artifact } from '@/store/chat/types';
import { ArtifactCardActions } from './card/ArtifactCardActions';
import { ArtifactCardHeader } from './card/ArtifactCardHeader';
import { ArtifactCardPanels } from './card/ArtifactCardPanels';
import { ArtifactInlinePreview } from './card/ArtifactInlinePreview';
import { ArtifactPublicationBanners } from './card/ArtifactPublicationBanners';
import { abilitiesOf } from './card/artifactKinds';
import { useArtifactActions } from './card/useArtifactActions';
import { useArtifactContent } from './card/useArtifactContent';
import { useArtifactPublishing } from './card/useArtifactPublishing';
import { useLazyText } from './card/useLazyText';
import { isOrganizePlanArtifact } from './organizePlanUtils';
import { PublishModal } from './PublishModal';

interface ArtifactCardProps {
  artifact: Artifact;
  onPreview?: (artifact: Artifact) => void;
  onDownload?: (artifact: Artifact) => void;
}

const ArtifactCard: React.FC<ArtifactCardProps> = ({ artifact, onPreview, onDownload }) => {
  const abilities = abilitiesOf(artifact.type);
  const publishing = useArtifactPublishing(artifact);
  const { current } = publishing;
  const isLargeFile = abilities.inlinePreview && artifact.size > LARGE_FILE_THRESHOLD;
  const { fetchContent, preloadHandlers } = useArtifactContent(artifact, isLargeFile);
  const actions = useArtifactActions(artifact, current, fetchContent, onDownload);

  const [inlineExpanded, setInlineExpanded] = useState(abilities.inlinePreview);
  const inline = useLazyText(abilities.inlinePreview && inlineExpanded && !isLargeFile, fetchContent);

  const [wechatOpen, setWechatOpen] = useState(false);
  const [organizeOpen, setOrganizeOpen] = useState(false);
  const hasWeChatDraft = artifact.type === 'html' && Boolean(artifact.file_path);
  const hasOrganizePlan = isOrganizePlanArtifact(artifact.filename);
  const closeWeChat = useCallback(() => setWechatOpen(false), []);

  const preview = () => onPreview?.(current);

  return (
    <>
      <div
        className={cn(
          'group relative rounded-xl overflow-hidden',
          'border border-gray-200/60 dark:border-gray-700/60',
          'bg-gradient-to-br from-white to-gray-50/50 dark:from-gray-800 dark:to-gray-850/50',
          'hover:border-gray-300 dark:hover:border-gray-600',
          'hover:shadow-md hover:shadow-gray-200/50 dark:hover:shadow-gray-900/50',
          'transition-all duration-200 ease-out',
          abilities.inlinePreview && inlineExpanded && 'sm:col-span-2',
        )}
        {...preloadHandlers}
      >
        {/* oxlint-disable-next-line jsx-a11y/no-static-element-interactions -- dynamic role is not resolvable by static analysis */}
        <div
          className="flex items-center gap-3 p-3 cursor-pointer"
          onClick={() => abilities.preview && preview()}
          role={abilities.preview ? 'button' : undefined}
          tabIndex={abilities.preview ? 0 : undefined}
          onKeyDown={activateOnKey}
        >
          <ArtifactCardHeader artifact={artifact} current={current} />
          <ArtifactCardActions
            artifact={artifact}
            abilities={abilities}
            publishing={publishing}
            actions={actions}
            inlineExpanded={inlineExpanded}
            onToggleInline={() => setInlineExpanded((expanded) => !expanded)}
            wechat={hasWeChatDraft ? { open: wechatOpen, toggle: () => setWechatOpen((open) => !open) } : null}
            organize={hasOrganizePlan ? { open: organizeOpen, toggle: () => setOrganizeOpen((open) => !open) } : null}
            onPreview={onPreview}
          />
        </div>

        <ArtifactPublicationBanners
          publications={publishing.publications}
          latestVersionId={current.latest_version_id}
          onRedeploy={publishing.openRedeploy}
        />

        {abilities.inlinePreview && inlineExpanded ? (
          <ArtifactInlinePreview
            artifact={artifact}
            isLargeFile={isLargeFile}
            loading={inline.loading}
            content={inline.text}
            onOpenFullscreen={preview}
          />
        ) : null}

        <ArtifactCardPanels
          current={current}
          fetchContent={fetchContent}
          wechatOpen={hasWeChatDraft && wechatOpen}
          organizeOpen={hasOrganizePlan && organizeOpen}
          onWeChatDone={closeWeChat}
        />
      </div>
      <PublishModal
        artifact={current}
        open={publishing.modalOpen}
        onClose={publishing.closeModal}
        onPublished={publishing.handlePublished}
        initialTargetId={publishing.modalTargetId}
      />
    </>
  );
};

export default ArtifactCard;
