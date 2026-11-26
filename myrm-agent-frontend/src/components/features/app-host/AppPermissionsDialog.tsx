'use client';

/**
 * [INPUT]
 * - @/lib/app-bridge (POS: declaration scopes and permission status of an installed app)
 * - @/services/artifactApps (POS: stores decisions, lists recent activity)
 * - @/services/appNotification (POS: asks the OS for notification permission)
 * - ./AppActivityList (POS: recent bridge activity of the app)
 *
 * [OUTPUT]
 * - AppPermissionsDialog: lets the user decide what an installed app may use; `review` asks about what is still undecided,
 *   `manage` is the standing page behind the host menu, with the app's recent activity.
 *
 * [POS]
 * The only place a decision is made. Every capability the page declares is a switch; saving stores one decision per
 * capability together with the exact scope shown here, so a later version that asks for more is asked again.
 */

import { useState } from 'react';
import { useTranslations } from 'next-intl';
import { Bell, Globe } from 'lucide-react';
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
import { Switch } from '@/components/primitives/switch';
import { declaredScope, type CapabilityStatus, type Declaration, type GrantRecord } from '@/lib/app-bridge/declaration';
import { BRIDGE_CAPABILITIES, type BridgeCapability } from '@/lib/app-bridge/protocol';
import { requestAppNotificationPermission } from '@/services/appNotification';
import { saveGrant } from '@/services/artifactApps';
import { AppActivityList } from './AppActivityList';

export type PermissionsMode = 'review' | 'manage';

interface AppPermissionsDialogProps {
  open: boolean;
  mode: PermissionsMode;
  artifactId: string;
  appName: string;
  versionSha256: string;
  declaration: Declaration;
  statuses: Record<BridgeCapability, CapabilityStatus>;
  onSaved: (grants: GrantRecord[]) => void;
  onClose: () => void;
}

const ICONS: Record<BridgeCapability, typeof Bell> = { notify: Bell, fetch: Globe };

export function AppPermissionsDialog(props: AppPermissionsDialogProps) {
  const { open, onClose } = props;
  return (
    <Dialog open={open} onOpenChange={(next) => !next && onClose()}>
      <DialogContent className="max-w-md">
        <PermissionsForm {...props} />
      </DialogContent>
    </Dialog>
  );
}

/** Mounted only while the dialog is open, so every opening starts from the stored decisions. */
function PermissionsForm({
  mode,
  artifactId,
  appName,
  versionSha256,
  declaration,
  statuses,
  onSaved,
  onClose,
}: AppPermissionsDialogProps) {
  const t = useTranslations('artifacts.apps.permissions');
  const declared = BRIDGE_CAPABILITIES.filter((capability) => statuses[capability] !== 'undeclared');
  // A capability the user has not decided on starts switched on: the question is "allow what it asks for?".
  const [choices, setChoices] = useState<Record<BridgeCapability, boolean>>(() => ({
    notify: statuses.notify !== 'denied',
    fetch: statuses.fetch !== 'denied',
  }));
  const [saving, setSaving] = useState(false);

  const save = async () => {
    setSaving(true);
    try {
      const saved: GrantRecord[] = [];
      for (const capability of declared) {
        saved.push(
          await saveGrant(artifactId, capability, {
            decision: choices[capability] ? 'allow' : 'deny',
            scope: declaredScope(capability, declaration) ?? [],
            version_sha256: versionSha256,
          }),
        );
      }
      onSaved(saved);
      if (choices.notify && declared.includes('notify')) {
        void requestAppNotificationPermission();
      }
      onClose();
    } catch {
      toast.error(t('saveFailed'));
    } finally {
      setSaving(false);
    }
  };

  return (
    <>
      <DialogHeader>
        <DialogTitle>{mode === 'review' ? t('reviewTitle', { name: appName }) : t('manageTitle')}</DialogTitle>
        <DialogDescription>
          {declared.length === 0 ? t('none') : t(mode === 'review' ? 'reviewHint' : 'manageHint')}
        </DialogDescription>
      </DialogHeader>

      {declared.length > 0 && (
        <ul className="space-y-3">
          {declared.map((capability) => {
            const Icon = ICONS[capability];
            const hosts = declaredScope(capability, declaration) ?? [];
            return (
              <li
                key={capability}
                className="flex items-start gap-3 rounded-xl border border-border/60 bg-muted/20 p-3"
              >
                <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
                <div className="min-w-0 flex-1">
                  <p className="text-sm font-medium">{t(`${capability}.title`)}</p>
                  <p className="text-xs text-muted-foreground">{t(`${capability}.description`)}</p>
                  {hosts.length > 0 && (
                    <ul className="mt-1.5 flex flex-wrap gap-1.5">
                      {hosts.map((host) => (
                        <li key={host} className="rounded-full bg-background px-2 py-0.5 text-xs text-foreground/80">
                          {host}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <Switch
                  checked={choices[capability]}
                  onCheckedChange={(checked) => setChoices((current) => ({ ...current, [capability]: checked }))}
                  aria-label={t(`${capability}.title`)}
                />
              </li>
            );
          })}
        </ul>
      )}

      {mode === 'manage' && <AppActivityList artifactId={artifactId} />}

      <DialogFooter className="gap-2 sm:gap-0">
        <Button variant="ghost" onClick={onClose} disabled={saving}>
          {mode === 'review' ? t('later') : t('cancel')}
        </Button>
        {declared.length > 0 && (
          <Button onClick={() => void save()} disabled={saving}>
            {saving ? t('saving') : t('save')}
          </Button>
        )}
      </DialogFooter>
    </>
  );
}
