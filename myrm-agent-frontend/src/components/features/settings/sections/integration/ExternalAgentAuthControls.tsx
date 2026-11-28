'use client';

import { memo, useCallback, useState } from 'react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';

import { Button } from '@/components/primitives/button';
import { IconLoader } from '@/components/features/icons/PremiumIcons';
import {
  logoutExternalAgent,
  type ExternalAgentAccount,
  type ExternalAgentAuthStatus,
} from '@/services/external-agents';

import ExternalAgentInstallDialog from './ExternalAgentInstallDialog';
import ExternalAgentLoginDialog from './ExternalAgentLoginDialog';
import ExternalAgentTerminalLoginDialog from './ExternalAgentTerminalLoginDialog';

interface Props {
  command: string;
  status: ExternalAgentAuthStatus | null;
  /** The named account the agent runs as; absent: the login the CLI keeps on this machine. */
  account?: string;
  /** What the server reports for that account, once it has. */
  accountState?: ExternalAgentAccount | null;
  onChanged: () => void;
}

/**
 * Per-agent controls: install while the CLI is missing, otherwise the login-state badge plus login / logout.
 * The login is the one its strategy names: the app drives it, or the user runs the CLI's own login in a terminal.
 * An agent that runs as a named account shows that account's login, and logs in and out of it. A hosted deployment
 * offers no login (agents run on an API key); it only shows a login left from before, so that it can be removed.
 * An agent the deployment does not run has no install or login, only a note saying so and, for a login left from
 * before, the way to remove it.
 */
const ExternalAgentAuthControls = memo(({ command, status, account, accountState = null, onChanged }: Props) => {
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
      const state = await logoutExternalAgent(status.backend, account);
      // The CLI's own report decides: a login it could not remove is still there.
      if (state.authenticated) {
        toast.error(t('logoutIncomplete'));
      } else {
        toast.success(t('logoutSuccess'));
      }
      onChanged();
    } catch {
      toast.error(t('loginFailed'));
    } finally {
      setLoggingOut(false);
    }
  }, [status, account, t, onChanged]);

  if (!status) {
    return null;
  }

  const authenticated = account ? (accountState?.authenticated ?? false) : status.authenticated;
  const logoutButton = (
    <Button variant="ghost" size="sm" className="h-6 px-2 text-[11px]" onClick={handleLogout} disabled={loggingOut}>
      {loggingOut && <IconLoader className="w-3 h-3 mr-1 animate-spin" />}
      {t('logout')}
    </Button>
  );

  // Nothing to install or sign in to: the deployment does not run this agent. A login left from before can still be removed.
  if (!status.availableInDeployment) {
    return (
      <div className="flex items-center gap-1.5">
        <span className="inline-flex items-center rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground">
          {t('blockedInDeployment')}
        </span>
        {authenticated && logoutButton}
      </div>
    );
  }

  const badgeTone = authenticated
    ? 'bg-green-500/10 text-green-600 dark:text-green-400'
    : 'bg-muted text-muted-foreground';
  const dotTone = authenticated ? 'bg-green-500' : 'bg-muted-foreground/40';
  const badgeLabel = authenticated ? t('badgeLoggedIn') : t('badgeLoggedOut');
  const showLoginState = status.installed && (status.subscriptionLoginAllowed || authenticated);

  return (
    <>
      {showLoginState && (
        <div className="flex items-center gap-1.5">
          {account && (
            <span
              title={account}
              className="max-w-24 truncate rounded-full bg-muted px-2 py-0.5 text-[11px] font-medium text-muted-foreground"
            >
              {account}
            </span>
          )}
          <span
            className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${badgeTone}`}
          >
            <span className={`h-1.5 w-1.5 rounded-full ${dotTone}`} />
            {badgeLabel}
          </span>
          {authenticated ? (
            logoutButton
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
      {status.installed &&
        status.subscriptionLoginAllowed &&
        (status.loginStrategy === 'terminal' ? (
          <ExternalAgentTerminalLoginDialog
            open={loginOpen}
            onOpenChange={setLoginOpen}
            status={status}
            account={account}
            onChanged={onChanged}
          />
        ) : (
          <ExternalAgentLoginDialog
            open={loginOpen}
            onOpenChange={setLoginOpen}
            command={command}
            status={status}
            account={account}
            onChanged={onChanged}
          />
        ))}
    </>
  );
});
ExternalAgentAuthControls.displayName = 'ExternalAgentAuthControls';

export default ExternalAgentAuthControls;
