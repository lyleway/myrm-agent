/**
 * [INPUT]
 * - @/services/notification::notificationService (POS: browser notification with in-app toast fallback)
 * - @/services/tauriNativeNotification::sendTauriNativeNotification (POS: desktop OS notification)
 * - @/lib/deploy-mode::isTauriRuntime (POS: desktop shell detection)
 *
 * [OUTPUT]
 * - showAppNotification: shows a notification an installed app asked for, wherever the user is looking.
 * - requestAppNotificationPermission: asks the OS for notification permission; call it from the click that allows `notify`.
 *
 * [POS]
 * Host-side delivery of `myrm.notify`. A focused window gets an in-app toast; an unfocused desktop window gets a native
 * notification; the browser follows `notificationService` (toast while visible, OS notification while hidden).
 */

import { isTauriRuntime } from '@/lib/deploy-mode';
import { notificationService } from '@/services/notification';
import { sendTauriNativeNotification } from '@/services/tauriNativeNotification';

export async function showAppNotification(title: string, body: string): Promise<void> {
  if (isTauriRuntime() && !document.hasFocus() && (await sendTauriNativeNotification({ title, body }))) {
    return;
  }
  notificationService.notify(title, { body });
}

export async function requestAppNotificationPermission(): Promise<void> {
  if (isTauriRuntime()) {
    try {
      const { isPermissionGranted, requestPermission } = await import('@tauri-apps/plugin-notification');
      if (!(await isPermissionGranted())) {
        await requestPermission();
      }
    } catch {
      // The notification is then delivered as a toast.
    }
    return;
  }
  await notificationService.requestPermission();
}
