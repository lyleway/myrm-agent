'use client';

/**
 * [INPUT]
 * - ./artifactUtils::deploymentHostname / isPublicationStale (POS: publication display helpers)
 * - @/services/hosting::ArtifactPublication (POS: a published copy of an artifact on one hosting target)
 *
 * [OUTPUT]
 * - ArtifactPublicationBanners: live-link chips for ready publications and one redeploy banner per publication whose
 *   copy is older than the artifact's latest version.
 *
 * [POS]
 * Publication status strip under an artifact card header.
 */

import { useTranslations } from 'next-intl';
import { ExternalLink } from 'lucide-react';
import { Button } from '@/components/primitives/button';
import { type ArtifactPublication } from '@/services/hosting';
import { deploymentHostname, isPublicationStale } from './artifactUtils';

interface ArtifactPublicationBannersProps {
  publications: ArtifactPublication[];
  latestVersionId: string | null | undefined;
  onRedeploy: (hostingTargetId: string) => void;
}

export function ArtifactPublicationBanners({
  publications,
  latestVersionId,
  onRedeploy,
}: ArtifactPublicationBannersProps) {
  const t = useTranslations('artifacts');
  const live = publications.filter((pub) => pub.publication_status === 'READY' && pub.publication_url);
  const stale = publications.filter((pub) => isPublicationStale(pub, latestVersionId));

  return (
    <>
      {live.length > 0 && (
        <div className="mx-3 mb-2 flex flex-wrap gap-2" onClick={(e) => e.stopPropagation()} role="presentation">
          {live.map((pub) => {
            const url = pub.publication_url;
            if (!url) {
              return null;
            }
            return (
              <button
                key={pub.id}
                type="button"
                className="inline-flex items-center gap-1 rounded-full border border-green-200 bg-green-50 px-2.5 py-1 text-xs text-green-800 dark:border-green-900/40 dark:bg-green-950/30 dark:text-green-200 hover:bg-green-100 dark:hover:bg-green-950/50"
                onClick={(e) => {
                  e.stopPropagation();
                  window.open(url, '_blank');
                }}
                title={t('publish.openLiveWithTarget', {
                  target: pub.hosting_target_name ?? pub.hosting_target_id,
                  hostname: deploymentHostname(url),
                })}
              >
                <ExternalLink className="h-3 w-3" />
                <span className="truncate max-w-[12rem]">
                  {pub.hosting_target_name ? `${pub.hosting_target_name} · ` : ''}
                  {deploymentHostname(url)}
                </span>
              </button>
            );
          })}
        </div>
      )}

      {stale.map((pub) => (
        <div
          key={pub.id}
          className="mx-3 mb-2 flex flex-col gap-2 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-900 sm:flex-row sm:items-center sm:justify-between dark:border-amber-900/40 dark:bg-amber-950/30 dark:text-amber-100"
          onClick={(e) => e.stopPropagation()}
          role="presentation"
        >
          <span className="min-w-0">
            {t('deploy.redeployBannerForTarget', {
              target: pub.hosting_target_name ?? pub.hosting_target_id,
            })}
          </span>
          <Button
            size="sm"
            variant="outline"
            className="h-7 shrink-0 text-xs"
            onClick={(e) => {
              e.stopPropagation();
              onRedeploy(pub.hosting_target_id);
            }}
          >
            {t('deploy.redeployAction')}
          </Button>
        </div>
      ))}
    </>
  );
}
