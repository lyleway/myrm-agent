'use client';

/**
 * [INPUT]
 * - ./artifactUtils::createArtifactSharePreview (POS: creates a read-only share link for an artifact)
 * - @/services/artifactShares::buildPublicArtifactShareUrl (POS: resolves a share path to a reachable URL)
 * - @/lib/utils/clipboardUtils::writeToClipboard (POS: clipboard write)
 *
 * [OUTPUT]
 * - ArtifactSharePopover: card action that creates a read-only share link (optional password, expiry) and copies it.
 *
 * [POS]
 * Share-link control of an artifact card; owns the form state so the card does not.
 */

import { useCallback, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Link2, Lock } from 'lucide-react';
import { toast } from 'sonner';
import { Button } from '@/components/primitives/button';
import { Input } from '@/components/primitives/input';
import { Label } from '@/components/primitives/label';
import { Popover, PopoverContent, PopoverTrigger } from '@/components/primitives/popover';
import { writeToClipboard } from '@/lib/utils/clipboardUtils';
import { buildPublicArtifactShareUrl } from '@/services/artifactShares';
import { createArtifactSharePreview } from './artifactUtils';

interface ArtifactSharePopoverProps {
  artifactId: string;
  artifactType: string;
}

export function ArtifactSharePopover({ artifactId, artifactType }: ArtifactSharePopoverProps) {
  const t = useTranslations('artifacts');
  const [shareLoading, setShareLoading] = useState(false);
  const [sharePopoverOpen, setSharePopoverOpen] = useState(false);
  const [sharePassword, setSharePassword] = useState('');
  const [shareTtlDays, setShareTtlDays] = useState(7);

  const handleSharePreview = useCallback(async () => {
    if (shareLoading) {
      return;
    }
    setShareLoading(true);
    try {
      const pwd = sharePassword.trim() || undefined;
      const result = await createArtifactSharePreview(artifactId, artifactType, {
        ttlDays: shareTtlDays,
        password: pwd,
      });
      const url = result.share_url ?? buildPublicArtifactShareUrl(result.share_path);
      await writeToClipboard(url);
      const descKey = result.password_protected
        ? 'sharePreview.successDescriptionProtected'
        : 'sharePreview.successDescription';
      toast.success(t('sharePreview.successTitle'), {
        description: t(descKey, { days: shareTtlDays }),
      });
      setSharePopoverOpen(false);
      setSharePassword('');
      setShareTtlDays(7);
    } catch (error: unknown) {
      const message = error instanceof Error ? error.message : t('sharePreview.failed');
      toast.error(message);
    } finally {
      setShareLoading(false);
    }
  }, [artifactId, artifactType, shareLoading, sharePassword, shareTtlDays, t]);

  return (
    <Popover open={sharePopoverOpen} onOpenChange={setSharePopoverOpen}>
      <PopoverTrigger asChild>
        <Button
          variant="ghost"
          size="icon"
          className="h-8 w-8 text-primary"
          onClick={(e) => e.stopPropagation()}
          title={t('sharePreview.open')}
        >
          <Link2 className="w-4 h-4" />
        </Button>
      </PopoverTrigger>
      <PopoverContent className="w-72 p-3 space-y-3" align="end" onClick={(e) => e.stopPropagation()}>
        <p className="text-sm font-medium">{t('sharePreview.title')}</p>
        <div className="space-y-1.5">
          <Label className="text-xs flex items-center gap-1">
            <Lock className="w-3 h-3" />
            {t('sharePreview.passwordLabel')}
          </Label>
          <Input
            type="password"
            value={sharePassword}
            onChange={(e) => setSharePassword(e.target.value)}
            placeholder={t('sharePreview.passwordPlaceholder')}
            className="h-8 text-sm"
            autoComplete="new-password"
          />
        </div>
        <div className="space-y-1.5">
          <Label className="text-xs">{t('sharePreview.ttlLabel')}</Label>
          <div className="flex gap-1.5">
            {[1, 7, 14, 30].map((d) => (
              <Button
                key={d}
                variant={shareTtlDays === d ? 'default' : 'outline'}
                size="sm"
                className="h-7 flex-1 text-xs px-0"
                onClick={() => setShareTtlDays(d)}
              >
                {t('sharePreview.ttlDays', { days: d })}
              </Button>
            ))}
          </div>
        </div>
        <Button
          className="w-full h-8 text-sm"
          disabled={shareLoading}
          onClick={() => {
            void handleSharePreview();
          }}
        >
          {shareLoading ? t('sharePreview.creating') : t('sharePreview.createBtn')}
        </Button>
      </PopoverContent>
    </Popover>
  );
}
