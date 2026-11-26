'use client';

/**
 * [INPUT]
 * - ./useAppHostData (POS: loads the app, its page and the user's decisions)
 * - ./useAppBridge (POS: serves the embedded page's requests)
 * - ./AppPermissionsDialog (POS: where permissions are decided)
 * - @/components/features/artifacts/renderers/MediaPreview::HtmlPreview (POS: sandboxed iframe with theme and storage)
 * - @/store/useArtifactAppsStore (POS: install registry)
 * - @/lib/desktop/appWindow (POS: desktop window control: close, keep on top)
 * - @/hooks/tauri/useIsAppWindow (POS: detects the app's own desktop window)
 * - @/hooks/ui/useFocusedMode (POS: whether the page is shown without the app shell)
 *
 * [OUTPUT]
 * - AppHostView: the page of one installed app: title bar with a permissions / keep-on-top / uninstall menu and a review
 *   reminder while a permission is undecided (icon only on narrow screens, with an accessible name), then the app.
 *
 * [POS]
 * Body of `/apps/[artifactId]`. The app runs in the same sandbox as any HTML artifact (scripts only, no network); the
 * extra it gets is `window.myrm`, which reaches only what the user allowed here. In its own desktop window there is no
 * app shell to return to, so leaving means closing the window, and the page fills the whole window instead of leaving
 * room for the shell's top bar.
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import { useTranslations } from 'next-intl';
import { MoreHorizontal, ShieldAlert } from 'lucide-react';
import { toast } from 'sonner';
import { HtmlPreview } from '@/components/features/artifacts/renderers/MediaPreview';
import { Button } from '@/components/primitives/button';
import {
  DropdownMenu,
  DropdownMenuCheckboxItem,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/primitives/dropdown-menu';
import { EmptyState } from '@/components/primitives/empty-state';
import { Skeleton } from '@/components/primitives/skeleton';
import { useIsAppWindow } from '@/hooks/tauri/useIsAppWindow';
import { useFocusedMode } from '@/hooks/ui/useFocusedMode';
import { confirmAction } from '@/lib/app-dialog';
import { BRIDGE_CAPABILITIES } from '@/lib/app-bridge/protocol';
import { closeAppWindow, setAppWindowAlwaysOnTop } from '@/lib/desktop/appWindow';
import { cn } from '@/lib/utils/classnameUtils';
import { useArtifactAppsStore } from '@/store/useArtifactAppsStore';
import { AppPermissionsDialog, type PermissionsMode } from './AppPermissionsDialog';
import { useAppBridge } from './useAppBridge';
import { useAppHostData } from './useAppHostData';

export function AppHostView({ artifactId }: { artifactId: string }) {
  const t = useTranslations('artifacts.apps');
  const router = useRouter();
  const uninstall = useArtifactAppsStore((state) => state.uninstall);
  const { state, detail, html, declaration, statuses, applyGrants } = useAppHostData(artifactId);
  const frameHostRef = useRef<HTMLDivElement>(null);
  const inAppWindow = useIsAppWindow();
  const withoutShell = useFocusedMode();
  const [keepOnTop, setKeepOnTop] = useState(false);
  const [dialogMode, setDialogMode] = useState<PermissionsMode | null>(null);
  const reviewedRef = useRef(false);

  const undecided = BRIDGE_CAPABILITIES.some((capability) => statuses[capability] === 'ask');

  useAppBridge({
    artifactId,
    appName: detail?.name ?? '',
    versionSha256: detail?.latest_version.sha256_hash ?? '',
    statuses,
    frameHostRef,
  });

  // The first question is asked once per visit; "Later" leaves the capabilities off and the banner below reopens it.
  useEffect(() => {
    if (state === 'ready' && undecided && !reviewedRef.current) {
      reviewedRef.current = true;
      setDialogMode('review');
    }
  }, [state, undecided]);

  const leave = useCallback(() => {
    if (!inAppWindow) {
      router.push('/artifacts');
      return;
    }
    void closeAppWindow().then((closed) => {
      if (!closed) {
        router.push('/artifacts');
      }
    });
  }, [inAppWindow, router]);

  const handleKeepOnTop = useCallback(
    async (next: boolean) => {
      if (await setAppWindowAlwaysOnTop(next)) {
        setKeepOnTop(next);
      } else {
        toast.error(t('keepOnTopFailed'));
      }
    },
    [t],
  );

  const handleUninstall = useCallback(async () => {
    if (!detail) {
      return;
    }
    const confirmed = await confirmAction({
      variant: 'destructive',
      title: t('uninstall.title', { name: detail.name }),
      description: t('uninstall.description'),
      confirmText: t('uninstall.confirm'),
    });
    if (!confirmed) {
      return;
    }
    try {
      await uninstall(artifactId);
      toast.success(t('uninstall.done'));
      leave();
    } catch {
      toast.error(t('uninstall.failed'));
    }
  }, [artifactId, detail, leave, t, uninstall]);

  if (state === 'loading') {
    return <Skeleton className="m-4 h-[60vh] rounded-2xl" />;
  }

  if (state !== 'ready' || !detail || html === null) {
    return (
      <EmptyState
        variant="dashed"
        title={t(state === 'not_installed' ? 'notInstalled.title' : 'loadFailed.title')}
        description={t(state === 'not_installed' ? 'notInstalled.description' : 'loadFailed.description')}
        action={
          <Button variant="outline" onClick={leave}>
            {t(inAppWindow ? 'closeWindow' : 'backToApps')}
          </Button>
        }
      />
    );
  }

  return (
    <div
      className={cn('flex min-h-0 w-full flex-col', withoutShell ? 'h-dvh' : 'h-[calc(100vh-4rem)]')}
      data-testid="app-host"
    >
      <header className="flex items-center gap-2 border-b border-border/60 px-4 py-2.5">
        <h1 className="min-w-0 flex-1 truncate text-sm font-semibold">{detail.name}</h1>
        {undecided && dialogMode === null && (
          <Button
            variant="ghost"
            size="sm"
            className="gap-1.5 text-amber-600 dark:text-amber-400"
            aria-label={t('undecided')}
            onClick={() => setDialogMode('review')}
          >
            <ShieldAlert className="h-4 w-4" aria-hidden />
            <span className="hidden sm:inline">{t('undecided')}</span>
          </Button>
        )}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="ghost" size="icon" className="h-8 w-8 rounded-full" aria-label={t('menu.label')}>
              <MoreHorizontal className="h-4 w-4" aria-hidden />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="rounded-xl">
            <DropdownMenuItem onSelect={() => setDialogMode('manage')}>{t('menu.permissions')}</DropdownMenuItem>
            {inAppWindow && (
              <DropdownMenuCheckboxItem checked={keepOnTop} onCheckedChange={(next) => void handleKeepOnTop(next)}>
                {t('keepOnTop')}
              </DropdownMenuCheckboxItem>
            )}
            <DropdownMenuItem
              className="text-destructive-text focus:text-destructive-text"
              onSelect={() => void handleUninstall()}
            >
              {t('menu.uninstall')}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </header>

      <div ref={frameHostRef} className="min-h-0 flex-1">
        <HtmlPreview content={html} storageNamespace={artifactId} chatId={detail.chat_id ?? undefined} />
      </div>

      <AppPermissionsDialog
        open={dialogMode !== null}
        mode={dialogMode ?? 'manage'}
        artifactId={artifactId}
        appName={detail.name}
        versionSha256={detail.latest_version.sha256_hash}
        declaration={declaration}
        statuses={statuses}
        onSaved={applyGrants}
        onClose={() => setDialogMode(null)}
      />
    </div>
  );
}
