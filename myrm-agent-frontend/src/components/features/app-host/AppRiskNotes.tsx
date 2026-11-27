'use client';

/**
 * [INPUT]
 * - @/lib/app-bridge/externalScripts::ExternalScript (POS: the scripts a page loads from other sites)
 *
 * [OUTPUT]
 * - AppRiskNotes: the notes under the permission list: nobody reviewed this code, where it may come from, which sites
 *   serve its scripts and which of those can change without a new request.
 *
 * [POS]
 * Part of `AppPermissionsDialog`. A permission covers one version of the page and says nothing about the code itself, so
 * the dialog says what the permission cannot: the code is not reviewed, it may not be from the user's own conversation,
 * and scripts from other sites are outside the version the user approved.
 */

import { useTranslations } from 'next-intl';
import { TriangleAlert } from 'lucide-react';
import type { ExternalScript } from '@/lib/app-bridge/externalScripts';

interface AppRiskNotesProps {
  /** The page did not come out of one of the user's conversations (imported file). */
  notFromConversation: boolean;
  scripts: readonly ExternalScript[];
}

export function AppRiskNotes({ notFromConversation, scripts }: AppRiskNotesProps) {
  const t = useTranslations('artifacts.apps.permissions.notes');
  const drifting = scripts.filter((script) => script.mayChange).map((script) => script.host);
  return (
    <div className="space-y-1.5 text-xs text-muted-foreground">
      <p>{t('unreviewed')}</p>
      {notFromConversation && (
        <p className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400" data-testid="app-note-imported">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {t('notFromConversation')}
        </p>
      )}
      {scripts.length > 0 && <p>{t('scripts', { hosts: scripts.map((script) => script.host).join(', ') })}</p>}
      {drifting.length > 0 && (
        <p className="flex items-start gap-1.5 text-amber-700 dark:text-amber-400" data-testid="app-note-drift">
          <TriangleAlert className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden />
          {t('drift', { hosts: drifting.join(', ') })}
        </p>
      )}
    </div>
  );
}
