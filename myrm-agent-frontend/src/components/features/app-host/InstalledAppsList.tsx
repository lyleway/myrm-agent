'use client';

/**
 * [INPUT]
 * - @/store/useArtifactAppsStore (POS: install registry)
 * - ./openApp (POS: opens an installed app)
 * - @/lib/utils/relativeTime::formatRelativeTime (POS: relative timestamps)
 *
 * [OUTPUT]
 * - InstalledAppsList: the user's installed apps as a compact list; renders nothing until there is one.
 *
 * [POS]
 * "Apps" section of the Artifacts page. Installing happens on an HTML artifact's card; this is where installed apps
 * are found again.
 */

import { useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { useLocale, useTranslations } from 'next-intl';
import { AppWindow } from 'lucide-react';
import { formatRelativeTime } from '@/lib/utils/relativeTime';
import { useArtifactAppsStore } from '@/store/useArtifactAppsStore';
import { openApp } from './openApp';

export function InstalledAppsList() {
  const t = useTranslations('artifacts.apps');
  const locale = useLocale();
  const router = useRouter();
  const apps = useArtifactAppsStore((state) => state.apps);
  const refresh = useArtifactAppsStore((state) => state.refresh);

  // Apps are installed and removed from other windows too; coming back to this one re-reads the registry.
  useEffect(() => {
    const reread = () => void refresh();
    reread();
    window.addEventListener('focus', reread);
    return () => window.removeEventListener('focus', reread);
  }, [refresh]);

  if (apps.length === 0) {
    return null;
  }

  return (
    <section aria-label={t('sectionTitle')} className="mb-5">
      <h3 className="mb-2 px-1 text-xs font-medium uppercase tracking-wide text-muted-foreground">
        {t('sectionTitle')}
      </h3>
      <ul className="space-y-1.5">
        {apps.map((app) => (
          <li key={app.artifact_id}>
            <button
              type="button"
              onClick={() => void openApp(app.artifact_id, app.name, router.push)}
              className="flex w-full items-center gap-3 rounded-xl border border-transparent bg-background p-2.5 text-left transition-colors hover:bg-muted/50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
            >
              <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
                <AppWindow className="h-4 w-4" aria-hidden />
              </span>
              <span className="min-w-0 flex-1">
                <span className="block truncate text-sm font-medium">{app.name}</span>
                <span className="block text-xs text-muted-foreground">
                  {formatRelativeTime(app.updated_at, locale)}
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
