'use client';

import { memo, useCallback, useState } from 'react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';

import { Button } from '@/components/primitives/button';
import { IconLoader } from '@/components/features/icons/PremiumIcons';
import { logoutExternalAgent, type ExternalAgentAuthStatus } from '@/services/external-agents';

import ExternalAgentInstallDialog from './ExternalAgentInstallDialog';
import ExternalAgentLoginDialog from './ExternalAgentLoginDialog';

interface AuthControlsProps {
  command: string;
  status: ExternalAgentAuthStatus | null;
  onChanged: () => void;
}

/**
 * Per-agent controls: install while the CLI is missing, otherwise the login-state badge plus login / logout.
 * A hosted deployment offers no login (agents run on an API key); it only shows a login left from before, so
 * that it can be removed.
 */
const ExternalAgentAuthControls = memo(({ command, status, onChanged }: AuthControlsProps) => {
  const t = useTranslations('settings.developer.externalAgents');
  const [loginOpen, setLoginOpen] = useState(false);
  const [installOpen, setInstallOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);

  const handleLogout = useCallback(async () => {
    if (!status) {
      return;
    }
    setLoggingOut(true);
    try {
      await logoutExternalAgent(status.backend);
      toast.success(t('logoutSuccess'));
      onChanged();
    } catch {
      toast.error(t('loginFailed'));
    } finally {
      setLoggingOut(false);
    }
  }, [status, t, onChanged]);

  if (!status) {
    return null;
  }

  const badgeTone = status.authenticated
    ? 'bg-green-500/10 text-green-600 dark:text-green-400'
    : 'bg-muted text-muted-foreground';
  const dotTone = status.authenticated ? 'bg-green-500' : 'bg-muted-foreground/40';
  const badgeLabel = status.authenticated ? t('badgeLoggedIn') : t('badgeLoggedOut');
  const showLoginState = status.installed && (status.subscriptionLoginAllowed || status.authenticated);

  return (
    <>
      {showLoginState && (
        <div className="flex items-center gap-1.5">
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${badgeTone}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${dotTone}`} />
            {badgeLabel}
          </span>
          {status.authenticated ? (
            <Button
              variant="ghost"
              size="sm"
              className="h-6 px-2 text-[11px]"
              onClick={handleLogout}
              disabled={loggingOut}
            >
              {loggingOut && <IconLoader className="w-3 h-3 mr-1 animate-spin" />}
              {t('logout')}
            </Button>
          ) : (
            <Button variant="ghost" size="sm" className="h-6 px-2 text-[11px]" onClick={() => setLoginOpen(true)}>
              {t('login')}
            </Button>
          )}
        </div>
      )}
      {!status.installed && (
        <Button variant="outline" size="sm" onClick={() => setInstallOpen(true)}>
          {t('install')}
        </Button>
      )}
      <ExternalAgentInstallDialog
        open={installOpen}
        onOpenChange={setInstallOpen}
        backend={status.backend}
        onInstalled={onChanged}
      />
      {status.installed && status.subscriptionLoginAllowed && (
        <ExternalAgentLoginDialog
          open={loginOpen}
          onOpenChange={setLoginOpen}
          command={command}
          status={status}
          onChanged={onChanged}
        />
      )}
    </>
  );
});
ExternalAgentAuthControls.displayName = 'ExternalAgentAuthControls';

export default ExternalAgentAuthControls;
