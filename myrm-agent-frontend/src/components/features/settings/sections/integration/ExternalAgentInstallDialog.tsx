'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';

import { Button } from '@/components/primitives/button';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/primitives/dialog';
import { IconLoader } from '@/components/features/icons/PremiumIcons';
import { streamExternalAgentInstall, type ExternalAgentAuthEvent } from '@/services/external-agents';

/** How long the success state stays visible before the dialog closes itself. */
const SUCCESS_CLOSE_DELAY_MS = 1500;

interface InstallDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  backend: string;
  onInstalled: () => void;
}

/**
 * Installs a backend CLI into the managed toolchain and streams the installer log.
 * Closing the dialog while the install runs cancels it on the server as well.
 */
const ExternalAgentInstallDialog = memo(({ open, onOpenChange, backend, onInstalled }: InstallDialogProps) => {
  const t = useTranslations('settings.developer.externalAgents');
  const [events, setEvents] = useState<ExternalAgentAuthEvent[]>([]);
  const [installing, setInstalling] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const startedRef = useRef(false);
  const closeTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const startInstall = useCallback(async () => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setEvents([]);
    setInstalling(true);
    try {
      for await (const event of streamExternalAgentInstall(backend, controller.signal)) {
        setEvents((prev) => [...prev, event]);
        if (event.type === 'success') {
          toast.success(t('installSuccess'));
          onInstalled();
          closeTimerRef.current = setTimeout(() => onOpenChange(false), SUCCESS_CLOSE_DELAY_MS);
        } else if (event.type === 'error') {
          toast.error(event.message);
        }
      }
    } catch {
      if (!controller.signal.aborted) {
        setEvents((prev) => [...prev, { type: 'error', message: t('installFailed') }]);
        toast.error(t('installFailed'));
      }
    } finally {
      if (abortRef.current === controller) {
        setInstalling(false);
      }
    }
  }, [backend, onInstalled, onOpenChange, t]);

  useEffect(() => {
    if (open && !startedRef.current) {
      startedRef.current = true;
      void startInstall();
    }
    if (!open) {
      startedRef.current = false;
      abortRef.current?.abort();
      clearTimeout(closeTimerRef.current);
      setEvents([]);
      setInstalling(false);
    }
  }, [open, startInstall]);

  useEffect(
    () => () => {
      startedRef.current = false;
      abortRef.current?.abort();
      clearTimeout(closeTimerRef.current);
    },
    [],
  );

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        abortRef.current?.abort();
        clearTimeout(closeTimerRef.current);
      }
      onOpenChange(next);
    },
    [onOpenChange],
  );

  const failed = !installing && events[events.length - 1]?.type === 'error';

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-[425px]">
        <DialogHeader>
          <DialogTitle>{t('installing', { backend })}</DialogTitle>
          <DialogDescription>{t('installingDesc')}</DialogDescription>
        </DialogHeader>
        <div className="flex flex-col gap-4 py-4">
          <div className="bg-muted text-muted-foreground flex max-h-[200px] flex-col gap-1 overflow-y-auto rounded-xl p-4 font-mono text-xs">
            {events.map((ev, i) => (
              <div
                key={i}
                className={
                  ev.type === 'error' ? 'text-destructive-text' : ev.type === 'success' ? 'text-green-500' : ''
                }
              >
                {ev.message}
              </div>
            ))}
            {installing && (
              <div className="flex items-center gap-2 text-blue-400">
                <IconLoader className="h-3 w-3 animate-spin" />
                <span>{t('installInProgress')}</span>
              </div>
            )}
          </div>
        </div>
        <DialogFooter>
          {failed && (
            <Button size="sm" onClick={() => void startInstall()}>
              {t('installRetry')}
            </Button>
          )}
          <Button variant="outline" size="sm" onClick={() => handleOpenChange(false)}>
            {installing ? t('cancel') : t('installClose')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});
ExternalAgentInstallDialog.displayName = 'ExternalAgentInstallDialog';

export default ExternalAgentInstallDialog;
