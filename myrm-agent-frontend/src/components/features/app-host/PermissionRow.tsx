'use client';

/**
 * [INPUT]
 * - @/lib/app-bridge/protocol::BridgeCapability (POS: capability vocabulary)
 * - @/components/primitives/switch (POS: the on/off control)
 *
 * [OUTPUT]
 * - PermissionRow: one declared capability in the permission dialog: what it does, where the data goes, what scope the
 *   page declared (sites, key combinations) and the switch.
 *
 * [POS]
 * Row of `AppPermissionsDialog`. The scope shown is the exact list that is stored with the decision.
 */

import { useTranslations } from 'next-intl';
import { Bell, BrainCircuit, Globe, Keyboard, PanelTop, PictureInPicture2 } from 'lucide-react';
import { Switch } from '@/components/primitives/switch';
import type { BridgeCapability } from '@/lib/app-bridge/protocol';

const ICONS: Record<BridgeCapability, typeof Bell> = {
  notify: Bell,
  fetch: Globe,
  ai: BrainCircuit,
  tray: PanelTop,
  shortcut: Keyboard,
  float: PictureInPicture2,
};

/** `CommandOrControl` is the one spelling the declaration allows; people read it as Cmd on a Mac and Ctrl elsewhere. */
function readableScope(capability: BridgeCapability, entry: string): string {
  return capability === 'shortcut' ? entry.replace('CommandOrControl', 'Cmd/Ctrl') : entry;
}

interface PermissionRowProps {
  capability: BridgeCapability;
  scope: readonly string[];
  checked: boolean;
  onCheckedChange: (checked: boolean) => void;
}

export function PermissionRow({ capability, scope, checked, onCheckedChange }: PermissionRowProps) {
  const t = useTranslations('artifacts.apps.permissions');
  const Icon = ICONS[capability];
  return (
    <li className="flex items-start gap-3 rounded-xl border border-border/60 bg-muted/20 p-3">
      <Icon className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" aria-hidden />
      <div className="min-w-0 flex-1">
        <p className="text-sm font-medium">{t(`${capability}.title`)}</p>
        <p className="text-xs text-muted-foreground">{t(`${capability}.description`)}</p>
        {scope.length > 0 && (
          <ul className="mt-1.5 flex flex-wrap gap-1.5">
            {scope.map((entry) => (
              <li key={entry} className="rounded-full bg-background px-2 py-0.5 text-xs text-foreground/80">
                {readableScope(capability, entry)}
              </li>
            ))}
          </ul>
        )}
        <p className="mt-1.5 text-xs text-muted-foreground/80">{t(`${capability}.destination`)}</p>
      </div>
      <Switch checked={checked} onCheckedChange={onCheckedChange} aria-label={t(`${capability}.title`)} />
    </li>
  );
}
