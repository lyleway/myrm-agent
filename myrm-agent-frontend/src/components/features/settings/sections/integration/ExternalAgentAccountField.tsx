'use client';

import { memo, useCallback, useState } from 'react';
import { useTranslations } from 'next-intl';
import { toast } from 'sonner';

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
import { IconLoader, IconTrash } from '@/components/features/icons/PremiumIcons';
import { Button } from '@/components/primitives/button';
import { Input } from '@/components/primitives/input';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/primitives/select';
import { ApiError } from '@/lib/api';
import { cn } from '@/lib/utils/classnameUtils';
import {
  createExternalAgentAccount,
  deleteExternalAgentAccount,
  type ExternalAgentAccount,
} from '@/services/external-agents';
import { isValidAccountName } from './externalAgentModel';

// Radix reserves the empty string for "no selection", so the two entries that are not accounts get names of their own.
const DEFAULT_ENTRY = '__default__';
const NEW_ENTRY = '__new__';

interface AccountFieldProps {
  /** The known agent the account is for. */
  backend: string;
  /** The chosen account; undefined is the login the CLI already has on this machine. */
  value: string | undefined;
  accounts: readonly ExternalAgentAccount[];
  onChange: (account: string | undefined) => void;
  /** An account was made, so the owner can read the list again. */
  onCreated: () => void;
  /** An account was removed, so the owner can read the list again and take it off the agents that ran as it. */
  onDeleted: (backend: string, name: string) => void;
}

/**
 * Which login an agent runs as: the one the CLI has on this machine, or a named account with a home of its own. Two
 * accounts of the same CLI never share a login, settings or history. Accounts are made and removed here, where they
 * are chosen.
 */
const ExternalAgentAccountField = memo(
  ({ backend, value, accounts, onChange, onCreated, onDeleted }: AccountFieldProps) => {
    const t = useTranslations('settings.developer.externalAgents');
    const [creating, setCreating] = useState(false);
    const [name, setName] = useState('');
    const [busy, setBusy] = useState(false);
    const [confirmingDelete, setConfirmingDelete] = useState(false);

    // A setting synced from another machine can name an account this machine has not made yet: keep it selectable.
    const names = [
      ...new Set([
        ...accounts.filter((account) => account.backend === backend).map((account) => account.name),
        ...(value ? [value] : []),
      ]),
    ];
    const nameInvalid = name !== '' && !isValidAccountName(name);

    const handleSelect = useCallback(
      (next: string) => {
        setCreating(next === NEW_ENTRY);
        if (next !== NEW_ENTRY) {
          onChange(next === DEFAULT_ENTRY ? undefined : next);
        }
      },
      [onChange],
    );

    const handleCancelCreate = useCallback(() => {
      setCreating(false);
      setName('');
    }, []);

    const handleCreate = useCallback(async () => {
      if (!isValidAccountName(name) || busy) {
        return;
      }
      setBusy(true);
      try {
        await createExternalAgentAccount(backend, name);
        onCreated();
        onChange(name);
        handleCancelCreate();
      } catch (error) {
        toast.error(t(error instanceof ApiError && error.code === 409 ? 'accountExists' : 'accountCreateFailed'));
      } finally {
        setBusy(false);
      }
    }, [backend, busy, handleCancelCreate, name, onChange, onCreated, t]);

    const handleDelete = useCallback(async () => {
      if (!value) {
        return;
      }
      try {
        await deleteExternalAgentAccount(backend, value);
      } catch (error) {
        // An account that is already gone is what was asked for.
        if (!(error instanceof ApiError && error.code === 404)) {
          toast.error(t('accountDeleteFailed'));
          return;
        }
      }
      toast.success(t('accountDeleted'));
      onChange(undefined);
      onDeleted(backend, value);
    }, [backend, onChange, onDeleted, t, value]);

    return (
      <div className="space-y-1.5" data-testid="external-agent-account">
        <label className="text-xs font-medium text-muted-foreground">{t('account')}</label>
        <div className="flex items-center gap-2">
          <div className="min-w-0 flex-1">
            <Select value={creating ? NEW_ENTRY : (value ?? DEFAULT_ENTRY)} onValueChange={handleSelect}>
              <SelectTrigger aria-label={t('account')}>
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value={DEFAULT_ENTRY}>{t('accountDefault')}</SelectItem>
                {names.map((account) => (
                  <SelectItem key={account} value={account}>
                    {account}
                  </SelectItem>
                ))}
                <SelectItem value={NEW_ENTRY}>{t('accountNew')}</SelectItem>
              </SelectContent>
            </Select>
          </div>
          {value && !creating && (
            <Button
              variant="ghost"
              size="icon"
              className="shrink-0 text-muted-foreground hover:text-red-500"
              aria-label={t('accountDelete')}
              onClick={() => setConfirmingDelete(true)}
            >
              <IconTrash className="h-4 w-4" />
            </Button>
          )}
        </div>

        {creating && (
          <div className="flex flex-col gap-2 rounded-lg border border-border bg-background/60 p-3 sm:flex-row sm:items-start">
            <div className="min-w-0 flex-1 space-y-1">
              <Input
                value={name}
                maxLength={32}
                aria-label={t('accountNameLabel')}
                placeholder={t('accountNamePlaceholder')}
                onChange={(e) => setName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    void handleCreate();
                  }
                }}
              />
              <p className={cn('text-[11px]', nameInvalid ? 'text-red-500' : 'text-muted-foreground/60')}>
                {t('accountNameHint')}
              </p>
            </div>
            <div className="flex gap-2">
              <Button size="sm" onClick={handleCreate} disabled={busy || !isValidAccountName(name)}>
                {busy && <IconLoader className="mr-1.5 h-3.5 w-3.5 animate-spin" />}
                {t('accountCreate')}
              </Button>
              <Button size="sm" variant="outline" onClick={handleCancelCreate}>
                {t('cancel')}
              </Button>
            </div>
          </div>
        )}

        <p className="text-[11px] text-muted-foreground/60">{t('accountHint')}</p>

        <AlertDialog open={confirmingDelete} onOpenChange={setConfirmingDelete}>
          <AlertDialogContent>
            <AlertDialogHeader>
              <AlertDialogTitle>{t('accountDelete')}</AlertDialogTitle>
              <AlertDialogDescription>{t('accountDeleteConfirm', { name: value ?? '' })}</AlertDialogDescription>
            </AlertDialogHeader>
            <AlertDialogFooter>
              <AlertDialogCancel>{t('cancel')}</AlertDialogCancel>
              <AlertDialogAction onClick={handleDelete}>{t('delete')}</AlertDialogAction>
            </AlertDialogFooter>
          </AlertDialogContent>
        </AlertDialog>
      </div>
    );
  },
);

ExternalAgentAccountField.displayName = 'ExternalAgentAccountField';

export default ExternalAgentAccountField;
