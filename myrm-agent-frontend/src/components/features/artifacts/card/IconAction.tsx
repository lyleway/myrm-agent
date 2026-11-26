/**
 * [INPUT]
 * - @/components/primitives/button (POS: Button)
 * - @/lib/utils/classnameUtils (POS: cn)
 *
 * [OUTPUT]
 * IconAction.
 *
 * [POS]
 * One icon button of the artifact card's action row. Clicks never reach the card's own
 * click-to-preview handler.
 */

import type { LucideIcon } from 'lucide-react';
import { Button } from '@/components/primitives/button';
import { cn } from '@/lib/utils/classnameUtils';

const NEUTRAL_TONE = 'text-gray-500 hover:text-gray-700 dark:text-gray-400 dark:hover:text-gray-200';

interface IconActionProps {
  icon: LucideIcon;
  title: string;
  onAction: () => void;
  /** Replaces the neutral colors, e.g. to show an open panel or a live state. */
  toneClass?: string;
  iconClassName?: string;
  disabled?: boolean;
  testId?: string;
}

export function IconAction({ icon: Icon, title, onAction, toneClass, iconClassName, disabled, testId }: IconActionProps) {
  return (
    <Button
      variant="ghost"
      size="icon"
      className={cn('h-8 w-8', toneClass ?? NEUTRAL_TONE)}
      disabled={disabled}
      onClick={(event) => {
        event.stopPropagation();
        onAction();
      }}
      title={title}
      data-testid={testId}
    >
      <Icon className={cn('w-4 h-4', iconClassName)} />
    </Button>
  );
}
