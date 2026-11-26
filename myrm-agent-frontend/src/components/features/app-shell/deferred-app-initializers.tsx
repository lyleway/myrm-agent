'use client';

import dynamic from 'next/dynamic';

import { useIsAppWindow } from '@/hooks/tauri/useIsAppWindow';

import DeferredMount from './deferred-mount';

const FlowPadModal = dynamic(() => import('./flow-pad-modal').then((module) => ({ default: module.FlowPadModal })), {
  ssr: false,
});

const ServiceWorkerRegistrar = dynamic(
  () => import('./service-worker-registrar').then((module) => ({ default: module.ServiceWorkerRegistrar })),
  { ssr: false },
);

const AppUpdatePrompt = dynamic(
  () => import('./app-update-prompt').then((module) => ({ default: module.AppUpdatePrompt })),
  { ssr: false },
);

const WhatsNewModal = dynamic(() => import('./whats-new-modal').then((module) => ({ default: module.WhatsNewModal })), {
  ssr: false,
});

const UpdateHandoffNotifier = dynamic(
  () => import('./update-handoff-notifier').then((module) => ({ default: module.UpdateHandoffNotifier })),
  { ssr: false },
);

const InstanceArchiveOutcomeNotifier = dynamic(
  () =>
    import('@/components/features/instance-archive/OutcomeNotifier').then((module) => ({
      default: module.OutcomeNotifier,
    })),
  { ssr: false },
);

const AppshotInitializer = dynamic(() => import('./appshot-initializer'), { ssr: false });

const VoicePttInitializer = dynamic(() => import('./voice-ptt-initializer'), { ssr: false });

export default function DeferredAppInitializers() {
  // An installed app's window only hosts the app. These are app-wide listeners and prompts (updates, global
  // shortcuts, capture) that belong to the main window; running them again per app window would duplicate them.
  if (useIsAppWindow()) {
    return null;
  }

  return (
    <DeferredMount>
      <FlowPadModal />
      <ServiceWorkerRegistrar />
      <AppUpdatePrompt />
      <WhatsNewModal />
      <UpdateHandoffNotifier />
      <InstanceArchiveOutcomeNotifier />
      <AppshotInitializer />
      <VoicePttInitializer />
    </DeferredMount>
  );
}
