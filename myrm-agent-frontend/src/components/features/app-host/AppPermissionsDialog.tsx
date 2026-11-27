'use client';

/**
 * [INPUT]
 * - @/lib/app-bridge (POS: declaration scopes and permission status of an installed app)
 * - @/services/artifactApps (POS: stores decisions, lists recent activity)
 * - @/services/appNotification (POS: asks the OS for notification permission)
 * - ./AppActivityList (POS: recent bridge activity of the app)
 * - ./PermissionRow / ./AppRiskNotes (POS: one capability; what a permission cannot tell about the code)
 *
 * [OUTPUT]
 * - AppPermissionsDialog: lets the user decide what an installed app may use; `review` asks about what is still undecided,
 *   `manage` is the standing page behind the host menu, with the app's recent activity.
 *
 * [POS]
 * The only place a decision is made. Every capability the page declares and this window can offer is a switch (the tray
 * item, key combinations and floating exist only in the app's own desktop window and are not listed elsewhere); saving
 * stores one decision per capability together with the exact scope shown here, so a later version that asks for more
 * is asked again.
 */

import { useState } from 'react';
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
import { Switch } from '@/components/primitives/switch';
import {
  declaredScope,
  offeredCapabilities,
  type CapabilityStatus,
  type Declaration,
  type GrantRecord,
} from '@/lib/app-bridge/declaration';
import type { ExternalScript } from '@/lib/app-bridge/externalScripts';
import { BRIDGE_CAPABILITIES, type BridgeCapability } from '@/lib/app-bridge/protocol';
import { requestAppNotificationPermission } from '@/services/appNotification';
import { saveGrant } from '@/services/artifactApps';
import { AppActivityList } from './AppActivityList';
import { AppRiskNotes } from './AppRiskNotes';
import { PermissionRow } from './PermissionRow';

export type PermissionsMode = 'review' | 'manage';

interface AppPermissionsDialogProps {
  open: boolean;
  mode: PermissionsMode;
  artifactId: string;
  appName: string;
  versionSha256: string;
  declaration: Declaration;
  statuses: Record<BridgeCapability, CapabilityStatus>;
  /** Whether this window can offer the capabilities that act on the desktop. */
  desktopAvailable: boolean;
  /** The page did not come out of one of the user's conversations. */
  notFromConversation: boolean;
  externalScripts: readonly ExternalScript[];
  onSaved: (grants: GrantRecord[]) => void;
  onClose: () => void;
}

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
  desktopAvailable,
  notFromConversation,
  externalScripts,
  onSaved,
  onClose,
}: AppPermissionsDialogProps) {
  const t = useTranslations('artifacts.apps.permissions');
  const declared = offeredCapabilities(statuses, desktopAvailable);
  const onlyOnDesktop = BRIDGE_CAPABILITIES.some((c) => statuses[c] !== 'undeclared' && !declared.includes(c));
  // A capability the user has not decided on starts switched on: the question is "allow what it asks for?".
  const [choices, setChoices] = useState<Record<BridgeCapability, boolean>>(
    () => Object.fromEntries(BRIDGE_CAPABILITIES.map((c) => [c, statuses[c] !== 'denied'])) as Record<BridgeCapability, boolean>,
  );
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
          {declared.length > 0 ? t(mode === 'review' ? 'reviewHint' : 'manageHint') : t(onlyOnDesktop ? 'desktopOnly' : 'none')}
        </DialogDescription>
      </DialogHeader>

      {declared.length > 0 && (
        <ul className="space-y-3">
          {declared.map((capability) => (
            <PermissionRow
              key={capability}
              capability={capability}
              scope={declaredScope(capability, declaration) ?? []}
              checked={choices[capability]}
              onCheckedChange={(checked) => setChoices((current) => ({ ...current, [capability]: checked }))}
            />
          ))}
        </ul>
      )}

      {declared.length > 0 && onlyOnDesktop && <p className="text-xs text-muted-foreground">{t('desktopOnly')}</p>}

      <AppRiskNotes notFromConversation={notFromConversation} scripts={externalScripts} />

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
