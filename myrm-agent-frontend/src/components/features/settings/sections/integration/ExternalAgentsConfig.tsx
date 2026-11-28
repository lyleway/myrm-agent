'use client';

import { memo, useState, useEffect, useCallback } from 'react';
import {
  IconBot,
  IconPencil,
  IconPlug,
  IconPlus,
  IconServer,
  IconTerminal,
  IconTrash,
} from '@/components/features/icons/PremiumIcons';
import { useTranslations } from 'next-intl';
import { Skeleton } from '@/components/primitives/skeleton';
import { toast } from 'sonner';

import { Switch } from '@/components/primitives/switch';
import { Button } from '@/components/primitives/button';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from '@/components/primitives/alert-dialog';
import { apiRequest } from '@/lib/api';
import { isLocalMode } from '@/lib/deploy-mode';
import {
  getConfigSyncManager,
  type ExternalAgentConfig,
  type ExternalAgentType,
  type ExternalAgentsConfigValue,
} from '@/services/config';
import {
  getExternalAgentAuthStatus,
  isBlockedInDeployment,
  type ExternalAgentAuthStatus,
} from '@/services/external-agents';
import ExternalAgentAuthControls from './ExternalAgentAuthControls';
import ExternalAgentForm from './ExternalAgentForm';
import {
  DEFAULT_AGENT,
  accountBackend,
  availablePresets,
  backendKeyFromCommand,
  cleanAgent,
  contractTransport,
  detachAccount,
  envRowProblems,
  toDraft,
  type ExternalAgentDraft,
} from './externalAgentModel';
import { useExternalAgentAccounts } from './useExternalAgentAccounts';
import { useExternalAgentCatalog } from './useExternalAgentCatalog';
import SettingsSection from '../SettingsSection';
import { cn } from '@/lib/utils/classnameUtils';
import { REVEAL_ON_HOVER } from '@/lib/utils/a11y';

const TYPE_ICONS: Record<ExternalAgentType, React.ElementType> = {
  cli: IconTerminal,
  acp: IconPlug,
  app_server: IconServer,
};

const syncManager = getConfigSyncManager();

const ExternalAgentsConfig = memo(() => {
  const t = useTranslations('settings.developer.externalAgents');
  const [agents, setAgents] = useState<ExternalAgentConfig[]>([]);
  const [editingIndex, setEditingIndex] = useState<number | null>(null);
  const [draft, setDraft] = useState<ExternalAgentDraft>(() => toDraft(DEFAULT_AGENT));
  const [deleteIndex, setDeleteIndex] = useState<number | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [isTesting, setIsTesting] = useState(false);
  const [authStatuses, setAuthStatuses] = useState<ExternalAgentAuthStatus[]>([]);
  const { accounts, refreshAccounts } = useExternalAgentAccounts();
  const known = useExternalAgentCatalog();

  const refreshAuthStatuses = useCallback(async () => {
    try {
      setAuthStatuses(await getExternalAgentAuthStatus());
    } catch {
      // Login state is advisory; never block the settings UI on it.
    }
  }, []);

  const refreshLogins = useCallback(() => {
    void refreshAuthStatuses();
    void refreshAccounts();
  }, [refreshAuthStatuses, refreshAccounts]);

  // Until the server has answered, assume a local deployment: the server turns subscription mode into an API key
  // by itself where login is not allowed, so a form opened early is never a way around it.
  const subscriptionLoginAllowed = authStatuses.every((s) => s.subscriptionLoginAllowed);

  const statusForCommand = useCallback(
    (command: string): ExternalAgentAuthStatus | null => {
      const key = backendKeyFromCommand(command, authStatuses);
      if (!key) {
        return null;
      }
      return authStatuses.find((s) => s.backend === key) ?? null;
    },
    [authStatuses],
  );

  useEffect(() => {
    (async () => {
      try {
        const config = syncManager.get('externalAgents');
        if (config?.agents) {
          setAgents(config.agents);
        }
      } catch {
        // No config yet
      } finally {
        setIsLoading(false);
      }
    })();
    void refreshAuthStatuses();
    void refreshAccounts();
  }, [refreshAuthStatuses, refreshAccounts]);

  const persist = useCallback((updated: ExternalAgentConfig[]) => {
    setAgents(updated);
    const value: ExternalAgentsConfigValue = { agents: updated };
    syncManager.set('externalAgents', value);
  }, []);

  const handleToggle = useCallback(
    (index: number) => {
      const updated = agents.map((a, i) => (i === index ? { ...a, enabled: !a.enabled } : a));
      persist(updated);
    },
    [agents, persist],
  );

  const handleStartAdd = useCallback(() => {
    setDraft(toDraft(DEFAULT_AGENT));
    setEditingIndex(-1);
  }, []);

  const handleStartEdit = useCallback(
    (index: number) => {
      setDraft(toDraft(agents[index]));
      setEditingIndex(index);
    },
    [agents],
  );

  const handleSave = useCallback(() => {
    const trimmedName = draft.name.trim();
    if (!trimmedName) {
      toast.error(t('nameRequired'));
      return;
    }
    if (!draft.command.trim()) {
      toast.error(t('commandRequired'));
      return;
    }
    if (isBlockedInDeployment(draft.command, authStatuses)) {
      toast.error(t('blockedInDeploymentSave'));
      return;
    }

    const duplicate = agents.some((a, i) => a.name === trimmedName && i !== editingIndex);
    if (duplicate) {
      toast.error(t('nameDuplicate'));
      return;
    }
    if (envRowProblems(draft.envRows).size > 0) {
      toast.error(t('envFixBeforeSave'));
      return;
    }

    const cleaned = cleanAgent(draft, known, subscriptionLoginAllowed);

    let updated: ExternalAgentConfig[];
    if (editingIndex === -1) {
      updated = [...agents, cleaned];
    } else {
      updated = agents.map((a, i) => (i === editingIndex ? cleaned : a));
    }
    persist(updated);
    setEditingIndex(null);
  }, [draft, editingIndex, agents, persist, subscriptionLoginAllowed, authStatuses, known, t]);

  // The agents that ran as a removed account go back to the login the CLI has on this machine.
  const handleAccountDeleted = useCallback(
    (backend: string, name: string) => {
      void refreshAccounts();
      const detached = detachAccount(agents, backend, name, known);
      if (detached !== agents) {
        persist([...detached]);
      }
    },
    [agents, known, persist, refreshAccounts],
  );

  const handleDelete = useCallback(() => {
    if (deleteIndex === null) {
      return;
    }
    const updated = agents.filter((_, i) => i !== deleteIndex);
    persist(updated);
    setDeleteIndex(null);
    if (editingIndex === deleteIndex) {
      setEditingIndex(null);
    }
  }, [deleteIndex, agents, persist, editingIndex]);

  const handleCancel = useCallback(() => {
    setEditingIndex(null);
  }, []);

  const handleTest = useCallback(async () => {
    const command = draft.command.trim();
    if (!command) {
      toast.error(t('commandRequired'));
      return;
    }
    setIsTesting(true);
    try {
      const result = await apiRequest<{ ok: boolean; message: string }>('/channels/manage/external-agents/test', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ command }),
      });
      if (result.ok) {
        toast.success(result.message);
      } else {
        toast.error(result.message);
      }
    } catch {
      toast.error(t('testFailed'));
    } finally {
      setIsTesting(false);
    }
  }, [draft.command, t]);

  if (isLoading) {
    return (
      <SettingsSection title={t('title')} description={t('description')}>
        <div className="space-y-3">
          {Array.from({ length: 3 }).map((_, i) => (
            <div key={i} className="flex items-center gap-3 p-3 rounded-lg border border-border/40">
              <Skeleton className="h-9 w-9 rounded-lg" />
              <div className="flex-1 space-y-1.5">
                <Skeleton className="h-4 w-28" />
                <Skeleton className="h-3 w-48" />
              </div>
            </div>
          ))}
        </div>
      </SettingsSection>
    );
  }

  const isEditing = editingIndex !== null;

  return (
    <>
      <SettingsSection
        title={t('title')}
        description={t('description')}
        action={
          !isEditing ? (
            <Button onClick={handleStartAdd} size="sm">
              <IconPlus className="w-4 h-4 mr-1.5" />
              {t('add')}
            </Button>
          ) : undefined
        }
      >
        {isLocalMode() && <p className="text-xs text-muted-foreground -mt-2">{t('tauriAutoDetect')}</p>}
        <p className="text-xs text-muted-foreground">{t('agentToggleHint')}</p>
        {authStatuses.some((s) => !s.availableInDeployment) && (
          <p className="text-xs text-muted-foreground">{t('blockedInDeploymentHint')}</p>
        )}

        {agents.length === 0 && !isEditing && (
          <div className="flex flex-col items-center justify-center py-8 text-center">
            <IconBot className="w-10 h-10 text-muted-foreground/40 mb-3" />
            <p className="text-sm text-muted-foreground">{t('empty')}</p>
            <p className="text-xs text-muted-foreground/60 mt-1">{t('emptyHint')}</p>
          </div>
        )}

        {agents.length > 0 && (
          <div className="space-y-2">
            {agents.map((agent, index) => {
              const TypeIcon = TYPE_ICONS[contractTransport(agent.command, known) ?? agent.type] ?? IconTerminal;
              const accountOf = accountBackend(agent.command, agent.authMode, subscriptionLoginAllowed, known);
              const account = accountOf !== null ? agent.account : undefined;
              const accountState = account
                ? (accounts.find((a) => a.backend === accountOf && a.name === account) ?? null)
                : null;
              const blocked = isBlockedInDeployment(agent.command, authStatuses);
              return (
                <div
                  key={`${agent.name}-${index}`}
                  className="flex items-center justify-between p-3 rounded-lg border border-border bg-background/50 hover:bg-muted/40 transition-colors group"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <div role="presentation" onClick={(e) => e.stopPropagation()}>
                      <Switch checked={agent.enabled} disabled={blocked} onCheckedChange={() => handleToggle(index)} />
                    </div>
                    <TypeIcon className="w-4 h-4 text-muted-foreground shrink-0" />
                    <div className="min-w-0">
                      <p className="text-sm font-medium truncate">{agent.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {agent.command} {(agent.args ?? []).join(' ')}
                      </p>
                    </div>
                  </div>
                  <div className="flex items-center gap-1 shrink-0">
                    <ExternalAgentAuthControls
                      command={agent.command}
                      status={statusForCommand(agent.command)}
                      account={account}
                      accountState={accountState}
                      onChanged={refreshLogins}
                    />
                    <button
                      onClick={() => handleStartEdit(index)}
                      aria-label={t('edit')}
                      className={cn('p-1.5 hover:bg-muted rounded transition-all', REVEAL_ON_HOVER)}
                    >
                      <IconPencil className="w-3.5 h-3.5 text-muted-foreground" />
                    </button>
                    <button
                      onClick={() => setDeleteIndex(index)}
                      aria-label={t('delete')}
                      className={cn('p-1.5 hover:bg-muted rounded transition-all', REVEAL_ON_HOVER)}
                    >
                      <IconTrash className="w-3.5 h-3.5 text-red-500" />
                    </button>
                  </div>
                </div>
              );
            })}
          </div>
        )}

        {isEditing && (
          <ExternalAgentForm
            draft={draft}
            fixedTransport={contractTransport(draft.command, known)}
            isNew={editingIndex === -1}
            presets={availablePresets(known)}
            known={known}
            isTesting={isTesting}
            subscriptionLoginAllowed={subscriptionLoginAllowed}
            accounts={accounts}
            onAccountCreated={refreshLogins}
            onAccountDeleted={handleAccountDeleted}
            onChange={setDraft}
            onSave={handleSave}
            onCancel={handleCancel}
            onTest={handleTest}
          />
        )}
      </SettingsSection>

      <AlertDialog open={deleteIndex !== null} onOpenChange={() => setDeleteIndex(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>{t('delete')}</AlertDialogTitle>
            <AlertDialogDescription>{t('confirmDelete')}</AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
            <AlertDialogAction onClick={handleDelete}>{t('delete')}</AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </>
  );
});

ExternalAgentsConfig.displayName = 'ExternalAgentsConfig';

export default ExternalAgentsConfig;
