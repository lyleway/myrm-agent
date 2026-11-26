'use client';

/**
 * [INPUT]
 * - ./useLazyText (POS: load the plan text once, keep it while the panel is closed)
 * - ../WeChatDraftPanel, ../OrganizePlanPanel (POS: the panels themselves)
 * - @/store/useChatStore (POS: chat id, workspace and agent the panels act for)
 * - @/store/chat/types (POS: Artifact)
 *
 * [OUTPUT]
 * ArtifactCardPanels.
 *
 * [POS]
 * Panels that open below an artifact card: the WeChat draft form for html files and the
 * workspace organize-plan editor. Stays mounted so a loaded plan survives closing.
 */

import { useTranslations } from 'next-intl';
import type { Artifact } from '@/store/chat/types';
import useChatStore from '@/store/useChatStore';
import { OrganizePlanPanel } from '../OrganizePlanPanel';
import { WeChatDraftPanel } from '../WeChatDraftPanel';
import { useLazyText } from './useLazyText';

interface ArtifactCardPanelsProps {
  current: Artifact;
  fetchContent: () => Promise<string | null>;
  wechatOpen: boolean;
  organizeOpen: boolean;
  onWeChatDone: () => void;
}

export function ArtifactCardPanels({
  current,
  fetchContent,
  wechatOpen,
  organizeOpen,
  onWeChatDone,
}: ArtifactCardPanelsProps) {
  const t = useTranslations('artifacts');
  const chatId = useChatStore((state) => state.chatId);
  const workspaceDir = useChatStore((state) => state.workspaceDir);
  const agentName = useChatStore((state) => state.agentConfig?.agentName);
  const presetName = useChatStore((state) => state.agentConfig?.presetName);
  const plan = useLazyText(organizeOpen, fetchContent);

  return (
    <>
      {wechatOpen && current.file_path ? (
        <WeChatDraftPanel
          artifactId={current.id}
          filename={current.filename}
          htmlPath={current.file_path}
          chatId={chatId ?? null}
          agentName={agentName}
          presetName={presetName}
          fetchInlineContent={fetchContent}
          onComplete={onWeChatDone}
        />
      ) : null}

      {organizeOpen ? (
        <div
          className="mx-3 mb-3 rounded-lg border border-border/60 bg-muted/20 p-3"
          onClick={(event) => event.stopPropagation()}
          role="presentation"
        >
          {plan.loading && !plan.text ? (
            <div className="flex items-center justify-center py-6">
              <div className="animate-spin w-5 h-5 border-2 border-muted-foreground/30 border-t-primary rounded-full" />
            </div>
          ) : plan.text ? (
            <OrganizePlanPanel workspace={workspaceDir ?? ''} planContent={plan.text} onPlanChange={plan.setText} />
          ) : (
            <p className="text-xs text-muted-foreground">{t('organizePlan.invalidPlan')}</p>
          )}
        </div>
      ) : null}
    </>
  );
}
