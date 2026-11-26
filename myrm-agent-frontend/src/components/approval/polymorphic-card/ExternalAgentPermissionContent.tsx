'use client';

import { useTranslations } from 'next-intl';
import {
  FilePen,
  FileText,
  FolderInput,
  Globe,
  Search,
  ShieldQuestion,
  Terminal,
  Trash2,
  type LucideIcon,
} from 'lucide-react';
import type { ApprovalPayload } from '@/store/useApprovalStore';

/** What each kind of action an external agent asks about looks like; an unknown kind reads as "other". */
const KINDS: Record<string, { icon: LucideIcon; labelKey: string }> = {
  execute: { icon: Terminal, labelKey: 'kinds.execute' },
  edit: { icon: FilePen, labelKey: 'kinds.edit' },
  delete: { icon: Trash2, labelKey: 'kinds.delete' },
  move: { icon: FolderInput, labelKey: 'kinds.move' },
  fetch: { icon: Globe, labelKey: 'kinds.fetch' },
  read: { icon: FileText, labelKey: 'kinds.read' },
  search: { icon: Search, labelKey: 'kinds.search' },
  other: { icon: ShieldQuestion, labelKey: 'kinds.other' },
};

/**
 * Review body of an external agent asking to act: who asks, what kind of action, the command or headline it gives, the
 * files it names and the workspace it runs in. What the agent passed along stays one tap away.
 */
export function ExternalAgentPermissionContent({ approval }: { approval: ApprovalPayload }) {
  const t = useTranslations('toolApproval.externalAgentPermission');
  const { agent, kind, title, command, paths, workspace, tool_input: toolInput } = approval.payload ?? {};
  const { icon: Icon, labelKey } = KINDS[kind ?? ''] ?? KINDS.other;
  const summary = command ?? title;
  // A command is already shown above; the rest of what the agent passed along (a file's new text, a URL) stays one tap away.
  const details =
    toolInput && Object.keys(toolInput).some((key) => key !== 'command')
      ? JSON.stringify(toolInput, null, 2)
      : undefined;

  return (
    <div className="space-y-4">
      <div className="flex items-start gap-3 rounded-2xl border border-amber-500/40 bg-amber-500/10 px-4 py-3">
        <Icon className="mt-0.5 h-5 w-5 shrink-0 text-amber-600 dark:text-amber-400" aria-hidden="true" />
        <div className="min-w-0">
          <h4 className="break-words text-sm font-semibold">{t('title', { agent: agent ?? '' })}</h4>
          <p className="mt-0.5 text-xs text-muted-foreground">{t(labelKey)}</p>
        </div>
      </div>

      {summary && (
        <pre className="max-h-48 overflow-auto whitespace-pre-wrap break-all rounded-xl bg-muted p-3 font-mono text-xs">
          {summary}
        </pre>
      )}

      {paths && (
        <div className="space-y-1.5">
          <p className="text-xs font-medium text-muted-foreground">{t('paths')}</p>
          <ul className="space-y-1">
            {paths.map((path) => (
              <li key={path} className="break-all rounded-lg bg-muted px-2.5 py-1.5 font-mono text-xs">
                {path}
              </li>
            ))}
          </ul>
        </div>
      )}

      {workspace && (
        <p className="break-all text-xs text-muted-foreground">
          {t('workspace')}: <span className="font-mono">{workspace}</span>
        </p>
      )}

      {details && (
        <details className="rounded-xl border border-border/60 px-3 py-2">
          <summary className="cursor-pointer text-xs font-medium text-muted-foreground">{t('details')}</summary>
          <pre className="mt-2 max-h-64 overflow-auto whitespace-pre-wrap break-all font-mono text-xs">{details}</pre>
        </details>
      )}

      <p className="text-xs text-muted-foreground">{t('refuseHint')}</p>
    </div>
  );
}
