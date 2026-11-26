'use client';

/**
 * [INPUT]
 * - @/store/useArtifactAppsStore (POS: install registry)
 * - @/components/features/app-host/openApp (POS: opens an installed app)
 *
 * [OUTPUT]
 * - ArtifactAppButton: card action that installs an HTML artifact as an app and opens it, or opens it if installed.
 *
 * [POS]
 * Entry point of the installed-app flow, shown on HTML artifact cards. Installing only registers the artifact; what the
 * page may use is decided on its own page the first time it is opened.
 */

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { AppWindow } from 'lucide-react';
import { toast } from 'sonner';
import { openApp } from '@/components/features/app-host/openApp';
import { Button } from '@/components/primitives/button';
import { cn } from '@/lib/utils/classnameUtils';
import { selectIsInstalled, useArtifactAppsStore } from '@/store/useArtifactAppsStore';

interface ArtifactAppButtonProps {
  artifactId: string;
  name: string;
}

export function ArtifactAppButton({ artifactId, name }: ArtifactAppButtonProps) {
  const t = useTranslations('artifacts.apps');
  const router = useRouter();
  const installed = useArtifactAppsStore(selectIsInstalled(artifactId));
  const loaded = useArtifactAppsStore((state) => state.loaded);
  const install = useArtifactAppsStore((state) => state.install);
  const refresh = useArtifactAppsStore((state) => state.refresh);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!loaded) {
      void refresh();
    }
  }, [loaded, refresh]);

  const handleClick = async () => {
    if (busy) {
      return;
    }
    setBusy(true);
    try {
      if (!installed) {
        await install(artifactId);
      }
      await openApp(artifactId, name, router.push);
    } catch {
      toast.error(t('installFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn(
        'h-8 w-8',
        installed ? 'text-primary' : 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200',
      )}
      disabled={busy}
      onClick={(event) => {
        event.stopPropagation();
        void handleClick();
      }}
      title={installed ? t('open') : t('install')}
      aria-label={installed ? t('open') : t('install')}
    >
      <AppWindow className={cn('h-4 w-4', busy && 'animate-pulse')} aria-hidden />
    </Button>
  );
}
