'use client';

/**
 * [INPUT]
 * - @/services/artifactApps::fetchAppActivity (POS: recent bridge activity of an installed app)
 * - @/lib/utils/relativeTime::formatRelativeTime (POS: relative timestamps)
 *
 * [OUTPUT]
 * - AppActivityList: what the app did with its permissions lately (capability, outcome, site), newest first.
 *
 * [POS]
 * The user's check on a granted app. The log holds metadata only, so this list can show the site a request went to
 * but never what was asked or answered.
 */

import { useEffect, useState } from 'react';
import { useLocale, useTranslations } from 'next-intl';
import { formatRelativeTime } from '@/lib/utils/relativeTime';
import { fetchAppActivity, type AppActivity } from '@/services/artifactApps';

const RECENT = 20;

export function AppActivityList({ artifactId }: { artifactId: string }) {
  const t = useTranslations('artifacts.apps.permissions');
  const locale = useLocale();
  const [entries, setEntries] = useState<AppActivity[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchAppActivity(artifactId, RECENT)
      .then((rows) => !cancelled && setEntries(rows))
      .catch(() => !cancelled && setEntries([]));
    return () => {
      cancelled = true;
    };
  }, [artifactId]);

  return (
    <section aria-label={t('activity.title')} className="space-y-2">
      <h3 className="text-xs font-medium uppercase tracking-wide text-muted-foreground">{t('activity.title')}</h3>
      {entries === null ? null : entries.length === 0 ? (
        <p className="text-xs text-muted-foreground">{t('activity.empty')}</p>
      ) : (
        <ul className="max-h-44 space-y-1.5 overflow-y-auto pr-1">
          {entries.map((entry) => (
            <li
              key={`${entry.created_at}-${entry.capability}-${entry.detail ?? ''}`}
              className="flex items-baseline justify-between gap-3 text-xs"
            >
              <span className="min-w-0 truncate">
                <span className="font-medium">{t(`${entry.capability}.title`)}</span>
                {entry.detail && <span className="text-muted-foreground"> · {entry.detail}</span>}
              </span>
              <span className="shrink-0 text-muted-foreground">
                {t(`activity.outcome.${entry.outcome}`)} · {formatRelativeTime(entry.created_at, locale)}
              </span>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
