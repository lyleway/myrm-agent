/**
 * [INPUT]
 * - @/lib/app-bridge (POS: page-to-host channel: capability gate, limiter, notify, proxied fetch, desktop surface)
 * - @/lib/desktop/appSurface (POS: the shell's events for this window)
 * - @/services/artifactApps (POS: HTTP client of the installed-app API)
 * - @/services/appNotification (POS: host-side delivery of app notifications)
 *
 * [OUTPUT]
 * - useAppBridge: serves the embedded app page's requests for as long as the host page is mounted, takes down what the
 *   page set up on the desktop when a permission is revoked or the host page goes away, and reports shell events
 *   (a pressed key combination) to the page.
 *
 * [POS]
 * React binding of the host channel. It decides who sent a message: only the window of the iframe inside `frameHostRef`
 * is answered, so other frames, extensions and the page's own parent chain cannot borrow the app's permissions. The
 * logic after that point lives in `createAppBridgeHost`.
 */

import { useEffect, useRef, type RefObject } from 'react';
import { createAppBridgeHost } from '@/lib/app-bridge/host';
import type { AppBridgeHost, AppSurfaceDeps } from '@/lib/app-bridge/hostTypes';
import type { CapabilityStatus } from '@/lib/app-bridge/declaration';
import { BRIDGE_CAPABILITIES, rpcEvent, type AppEvent, type BridgeCapability } from '@/lib/app-bridge/protocol';
import { onAppSurfaceEvent } from '@/lib/desktop/appSurface';
import { showAppNotification } from '@/services/appNotification';
import { proxyAppAi, proxyAppFetch, reportAppActivity } from '@/services/artifactApps';

interface UseAppBridgeOptions {
  artifactId: string;
  appName: string;
  versionSha256: string;
  statuses: Record<BridgeCapability, CapabilityStatus>;
  declaredShortcuts: readonly string[];
  /** Element that contains the app's iframe. */
  frameHostRef: RefObject<HTMLElement | null>;
  /** The desktop window's side of tray / shortcut / float; `null` anywhere else. Keep the identity stable. */
  surface: AppSurfaceDeps | null;
  /** The page asked for the window to float, or stopped; keeps the host's own menu in step. */
  onFloatChange: (on: boolean) => void;
  /** The tray menu's "Permissions" item. */
  onOpenPermissions: () => void;
}

export function useAppBridge({
  artifactId,
  appName,
  versionSha256,
  statuses,
  declaredShortcuts,
  frameHostRef,
  surface,
  onFloatChange,
  onOpenPermissions,
}: UseAppBridgeOptions): void {
  // Decisions and callbacks are read at call time, so saving a permission takes effect without reloading the page.
  const live = useRef({ statuses, declaredShortcuts, onFloatChange, onOpenPermissions });
  const hostRef = useRef<AppBridgeHost | null>(null);
  const previousStatuses = useRef(statuses);

  useEffect(() => {
    live.current = { statuses, declaredShortcuts, onFloatChange, onOpenPermissions };
  }, [statuses, declaredShortcuts, onFloatChange, onOpenPermissions]);

  useEffect(() => {
    const frameWindow = () => frameHostRef.current?.querySelector('iframe')?.contentWindow ?? null;
    // The sandboxed page has an opaque origin, so '*' is the only target that reaches it.
    const tell = (event: AppEvent) => frameWindow()?.postMessage(rpcEvent(event), '*');

    const host = createAppBridgeHost({
      appName,
      versionSha256,
      getStatuses: () => live.current.statuses,
      getDeclaredShortcuts: () => live.current.declaredShortcuts,
      proxyFetch: (request) => proxyAppFetch(artifactId, request),
      proxyAi: (request) => proxyAppAi(artifactId, request),
      surface: surface && {
        ...surface,
        setFloat: async (on) => {
          const done = await surface.setFloat(on);
          if (done) {
            live.current.onFloatChange(on);
          }
          return done;
        },
      },
      reportActivity: (activity) => {
        void reportAppActivity(artifactId, versionSha256, activity).catch(() => undefined);
      },
      showNotification: showAppNotification,
    });
    hostRef.current = host;

    const onMessage = (event: MessageEvent) => {
      const target = frameWindow();
      if (!target || event.source !== target) {
        return;
      }
      void host.handle(event.data).then((reply) => {
        if (reply) {
          target.postMessage(reply, '*');
        }
      });
    };
    window.addEventListener('message', onMessage);

    // The subscription finishes asynchronously: when the page went away first, it is cancelled on arrival.
    const events: { stop: (() => void) | null; disposed: boolean } = { stop: null, disposed: false };
    if (surface) {
      void onAppSurfaceEvent({
        onShortcut: (accelerator) => {
          if (live.current.statuses.shortcut === 'allowed') {
            tell({ name: 'shortcut', data: { accelerator } });
          }
        },
        onOpenPermissions: () => live.current.onOpenPermissions(),
      }).then((stop) => {
        if (events.disposed) {
          stop();
        } else {
          events.stop = stop;
        }
      });
    }

    return () => {
      events.disposed = true;
      window.removeEventListener('message', onMessage);
      events.stop?.();
      hostRef.current = null;
      void host.dispose();
    };
  }, [artifactId, appName, versionSha256, frameHostRef, surface]);

  // A permission that was allowed and no longer is: the desktop side comes down at once and the page is told which one.
  useEffect(() => {
    const before = previousStatuses.current;
    previousStatuses.current = statuses;
    for (const capability of BRIDGE_CAPABILITIES) {
      if (before[capability] === 'allowed' && statuses[capability] !== 'allowed') {
        void hostRef.current?.revoke(capability);
        frameHostRef.current
          ?.querySelector('iframe')
          ?.contentWindow?.postMessage(rpcEvent({ name: 'permission-revoked', data: { capability } }), '*');
      }
    }
  }, [statuses, frameHostRef]);
}
