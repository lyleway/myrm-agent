'use client';

import { memo, useCallback, useEffect, useRef, useState } from 'react';
import { useTranslations } from 'next-intl';
import { Check, Copy } from 'lucide-react';
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
import { ToggleGroup, ToggleGroupItem } from '@/components/primitives/toggle-group';
import { IconLoader } from '@/components/features/icons/PremiumIcons';
import { useCopyFeedback } from '@/hooks/shared/useCopyFeedback';
import {
  getExternalAgentTerminalLogin,
  type ExternalAgentAuthStatus,
  type ExternalAgentShell,
  type ExternalAgentTerminalLogin,
} from '@/services/external-agents';

/** How long the dialog waits before asking again whether the login in the terminal has finished. */
const RECHECK_INTERVAL_MS = 4000;
const SUCCESS_CLOSE_DELAY_MS = 1500;

const SHELL_ORDER: readonly ExternalAgentShell[] = ['posix', 'powershell', 'cmd'];
const SHELL_LABEL_KEY = {
  posix: 'shellPosix',
  powershell: 'shellPowershell',
  cmd: 'shellCmd',
} as const satisfies Record<ExternalAgentShell, string>;

interface Props {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  status: ExternalAgentAuthStatus;
  /** The named account to log in to; absent: the login the CLI keeps on this machine. */
  account?: string;
  onChanged: () => void;
}

/** The shell whose command is shown: the one the user picked, else the host's own, else the first the server could spell. */
function shellToShow(
  login: ExternalAgentTerminalLogin | null,
  picked: ExternalAgentShell | null,
): ExternalAgentShell | undefined {
  if (!login) {
    return undefined;
  }
  if (picked && login.commands[picked]) {
    return picked;
  }
  return login.commands[login.defaultShell] ? login.defaultShell : SHELL_ORDER.find((shell) => login.commands[shell]);
}

/**
 * The login of an agent whose login the user runs in a terminal of their own: the official command, spelled for the
 * user's shell and moved into the account's home, then a check of what the CLI reports. The app never sees the
 * credential, so it asks the CLI who is logged in, by itself while the dialog is open and on demand.
 */
const ExternalAgentTerminalLoginDialog = memo(({ open, onOpenChange, status, account, onChanged }: Props) => {
  const t = useTranslations('settings.developer.externalAgents');
  const { copied, copy } = useCopyFeedback();
  const [login, setLogin] = useState<ExternalAgentTerminalLogin | null>(null);
  const [loadFailed, setLoadFailed] = useState(false);
  const [checking, setChecking] = useState(false);
  const [picked, setPicked] = useState<ExternalAgentShell | null>(null);
  const [round, setRound] = useState(0);
  // The checks outlive a render, so what they call is read from here instead of restarting them when a parent re-renders.
  const latest = useRef({ onChanged, onOpenChange, t });
  const backend = status.backend;

  useEffect(() => {
    latest.current = { onChanged, onOpenChange, t };
  });

  useEffect(() => {
    if (!open) {
      setLogin(null);
      setLoadFailed(false);
      setChecking(false);
      setPicked(null);
      return undefined;
    }
    let stopped = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    // Only a check the user asked for shows as one; the ones the dialog makes by itself stay quiet.
    const check = async (asked: boolean) => {
      if (asked) {
        setChecking(true);
      }
      try {
        const next = await getExternalAgentTerminalLogin(backend, account);
        if (stopped) {
          return;
        }
        setLogin(next);
        setLoadFailed(false);
        setChecking(false);
        if (next.authenticated) {
          toast.success(latest.current.t('loginSuccess'));
          latest.current.onChanged();
          timer = setTimeout(() => latest.current.onOpenChange(false), SUCCESS_CLOSE_DELAY_MS);
        } else {
          timer = setTimeout(() => void check(false), RECHECK_INTERVAL_MS);
        }
      } catch {
        if (!stopped) {
          setLoadFailed(true);
          setChecking(false);
        }
      }
    };
    void check(round > 0);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [open, backend, account, round]);

  const recheck = useCallback(() => setRound((value) => value + 1), []);

  const shells = SHELL_ORDER.filter((shell) => login?.commands[shell]);
  const shell = shellToShow(login, picked);
  const command = shell ? login?.commands[shell] : undefined;
  const copyLabel = copied ? t('terminalLoginCopied') : t('terminalLoginCopy');
  // Variables set in PowerShell or cmd stay for the whole window; a POSIX shell sets them for the one command only.
  const sessionNote = account !== undefined && shell !== undefined && shell !== 'posix';

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>
            {account ? t('loginTitleAccount', { backend, account }) : t('loginTitle', { backend })}
          </DialogTitle>
          <DialogDescription>{t('terminalLoginHint', { backend })}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          {command === undefined && !loadFailed && (
            <div className="flex items-center gap-2 text-sm text-muted-foreground">
              <IconLoader className="w-3.5 h-3.5 animate-spin" />
              {t('terminalLoginPreparing')}
            </div>
          )}

          {command !== undefined && (
            <>
              {shells.length > 1 && (
                <ToggleGroup
                  type="single"
                  value={shell}
                  onValueChange={(value) => value && setPicked(value as ExternalAgentShell)}
                  aria-label={t('terminalLoginShell')}
                  className="flex-wrap justify-start gap-0.5 rounded-xl border border-border bg-muted/30 p-0.5"
                >
                  {shells.map((option) => (
                    <ToggleGroupItem
                      key={option}
                      value={option}
                      className="h-7 rounded-lg px-3 text-xs data-[state=on]:bg-background data-[state=on]:text-foreground"
                    >
                      {t(SHELL_LABEL_KEY[option])}
                    </ToggleGroupItem>
                  ))}
                </ToggleGroup>
              )}

              <div className="relative rounded-xl border border-border bg-muted/40">
                <pre className="max-h-40 overflow-auto whitespace-pre-wrap break-all p-3 pr-11 font-mono text-xs leading-relaxed text-foreground">
                  <code>{command}</code>
                </pre>
                <button
                  type="button"
                  onClick={() => void copy(command)}
                  title={copyLabel}
                  aria-label={copyLabel}
                  className="absolute right-2 top-2 rounded-lg p-1.5 text-muted-foreground transition-colors hover:bg-background hover:text-foreground"
                >
                  {copied ? <Check className="h-4 w-4 text-emerald-500" /> : <Copy className="h-4 w-4" />}
                </button>
              </div>

              <ol className="list-decimal space-y-1 pl-5 text-sm text-muted-foreground">
                <li>{t('terminalLoginStepRun')}</li>
                <li>{t('terminalLoginStepFinish')}</li>
              </ol>
              {sessionNote && <p className="text-xs text-muted-foreground">{t('terminalLoginWindowNote')}</p>}

              {login?.authenticated ? (
                <p className="text-sm text-green-600 dark:text-green-400">{t('loginSuccess')}</p>
              ) : (
                !loadFailed && (
                  <div className="flex items-center gap-2 text-sm text-muted-foreground">
                    <IconLoader className="w-3.5 h-3.5 animate-spin" />
                    {t('terminalLoginWaiting')}
                  </div>
                )
              )}
            </>
          )}

          {loadFailed && (
            <div className="flex flex-wrap items-center gap-2">
              <p className="text-sm text-red-600 dark:text-red-400">{t('terminalLoginLoadFailed', { backend })}</p>
              <Button variant="outline" size="sm" className="h-6 px-2 text-xs" onClick={recheck}>
                {t('loginRetry')}
              </Button>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" size="sm" onClick={recheck} disabled={checking}>
            {checking && <IconLoader className="w-3.5 h-3.5 mr-1.5 animate-spin" />}
            {t('terminalLoginCheck')}
          </Button>
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            {t('loginClose')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
});
ExternalAgentTerminalLoginDialog.displayName = 'ExternalAgentTerminalLoginDialog';

export default ExternalAgentTerminalLoginDialog;
