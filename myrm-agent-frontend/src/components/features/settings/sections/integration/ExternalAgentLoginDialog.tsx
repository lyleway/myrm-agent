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
import { isLoopbackDevHost, isRemoteGatewayActive, isTauriRuntime } from '@/lib/deploy-mode';
import {
  streamExternalAgentLogin,
  type ExternalAgentAuthEvent,
  type ExternalAgentAuthStatus,
} from '@/services/external-agents';

/** Whether the browser that signs in is on another machine than the CLI: behind a remote gateway, or on a host that is not this one. */
function browserIsRemote(): boolean {
  return isRemoteGatewayActive() || !(isTauriRuntime() || isLoopbackDevHost());
}

/** The latest `url` or `code` the CLI has shown. A CLI may print the link and the code as separate lines. */
function latestPromptField(events: readonly ExternalAgentAuthEvent[], field: 'url' | 'code'): string | undefined {
  for (let index = events.length - 1; index >= 0; index -= 1) {
    const event = events[index];
    if (event.type === 'prompt' && event[field]) {
      return event[field];
    }
  }
  return undefined;
}

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  command: string;
  status: ExternalAgentAuthStatus;
  /** The named account to log in to; absent: the login the CLI keeps on this machine. */
  account?: string;
  onChanged: () => void;
}

/** The login of an agent the app drives itself: it shows the link or device code the CLI prints and waits for the CLI to finish. */
const ExternalAgentLoginDialog = memo(({ open, onOpenChange, command, status, account, onChanged }: Props) => {
  const t = useTranslations('settings.developer.externalAgents');
  const [events, setEvents] = useState<ExternalAgentAuthEvent[]>([]);
  const [running, setRunning] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const startedRef = useRef(false);
  const autoCloseRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);

  const backend = status.backend;

  const startLogin = useCallback(async () => {
    setEvents([]);
    setRunning(true);
    const controller = new AbortController();
    abortRef.current = controller;
    try {
      await streamExternalAgentLogin(
        { command, backend, account, remote: browserIsRemote() },
        (event) => {
          setEvents((prev) => [...prev, event]);
          if (event.type === 'success') {
            toast.success(t('loginSuccess'));
            onChanged();
            autoCloseRef.current = setTimeout(() => onOpenChange(false), 1500);
          } else if (event.type === 'error') {
            toast.error(event.message || t('loginFailed'));
          }
        },
        controller.signal,
      );
    } catch {
      if (!controller.signal.aborted) {
        setEvents((prev) => [...prev, { type: 'error', message: t('loginFailed') }]);
      }
    } finally {
      setRunning(false);
    }
  }, [command, backend, account, t, onChanged, onOpenChange]);

  useEffect(() => {
    if (open && !startedRef.current) {
      startedRef.current = true;
      void startLogin();
    }
    if (!open) {
      startedRef.current = false;
      abortRef.current?.abort();
      clearTimeout(autoCloseRef.current);
      setEvents([]);
    }
  }, [open, startLogin]);

  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next) {
        abortRef.current?.abort();
      }
      onOpenChange(next);
    },
    [onOpenChange],
  );

  const link = latestPromptField(events, 'url');
  const deviceCode = latestPromptField(events, 'code');
  const last = events[events.length - 1];

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {account ? t('loginTitleAccount', { backend, account }) : t('loginTitle', { backend })}
          </DialogTitle>
          <DialogDescription>{t('authModeHint')}</DialogDescription>
        </DialogHeader>

        <div className="space-y-2">
          {running && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <IconLoader className="w-3.5 h-3.5 animate-spin" />
              {last?.message || t('loginConnecting')}
            </div>
          )}
          {(link || deviceCode) && (
            <div className="space-y-1.5 rounded-md border border-border p-3 text-sm">
              <p className="text-muted-foreground">{t('loginOpenUrl')}</p>
              {link && (
                <a href={link} target="_blank" rel="noreferrer" className="text-primary break-all underline">
                  {link}
                </a>
              )}
              {deviceCode && <p className="font-mono text-base tracking-widest">{deviceCode}</p>}
            </div>
          )}
          {last?.type === 'success' && (
            <p className="text-sm text-green-600 dark:text-green-400">{t('loginSuccess')}</p>
          )}
          {last?.type === 'error' && (
            <div className="flex items-center gap-2">
              <p className="text-sm text-red-600 dark:text-red-400">{last.message}</p>
              <Button variant="outline" size="sm" className="h-6 px-2 text-xs" onClick={startLogin}>
                {t('loginRetry')}
              </Button>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={() => handleOpenChange(false)}>
            {t('loginClose')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});
ExternalAgentLoginDialog.displayName = 'ExternalAgentLoginDialog';

export default ExternalAgentLoginDialog;
