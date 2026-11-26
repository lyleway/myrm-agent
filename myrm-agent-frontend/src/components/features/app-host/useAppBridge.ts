/**
 * [INPUT]
 * - @/lib/app-bridge (POS: page-to-host channel: capability gate, limiter, notify and proxied fetch)
 * - @/services/artifactApps (POS: HTTP client of the installed-app API)
 * - @/services/appNotification (POS: host-side delivery of app notifications)
 *
 * [OUTPUT]
 * - useAppBridge: serves the embedded app page's requests for as long as the host page is mounted.
 *
 * [POS]
 * React binding of the host channel. It decides who sent a message: only the window of the iframe inside `frameHostRef`
 * is answered, so other frames, extensions and the page's own parent chain cannot borrow the app's permissions. The
 * logic after that point lives in `createAppBridgeHost`.
 */

import { useEffect, useRef, type RefObject } from 'react';
import { createAppBridgeHost } from '@/lib/app-bridge/host';
import type { CapabilityStatus } from '@/lib/app-bridge/declaration';
import type { BridgeCapability } from '@/lib/app-bridge/protocol';
import { showAppNotification } from '@/services/appNotification';
import { proxyAppFetch, reportAppActivity } from '@/services/artifactApps';

interface UseAppBridgeOptions {
  artifactId: string;
  appName: string;
  versionSha256: string;
  statuses: Record<BridgeCapability, CapabilityStatus>;
  /** Element that contains the app's iframe. */
  frameHostRef: RefObject<HTMLElement | null>;
}

export function useAppBridge({
  artifactId,
  appName,
  versionSha256,
  statuses,
  frameHostRef,
}: UseAppBridgeOptions): void {
  // Decisions are read at call time, so saving a permission takes effect without reloading the page.
  const statusesRef = useRef(statuses);
  useEffect(() => {
    statusesRef.current = statuses;
  }, [statuses]);

  useEffect(() => {
    const handle = createAppBridgeHost({
      appName,
      versionSha256,
      getStatuses: () => statusesRef.current,
      proxyFetch: (request) => proxyAppFetch(artifactId, request),
      reportActivity: (activity) => {
        void reportAppActivity(artifactId, versionSha256, activity).catch(() => undefined);
      },
      showNotification: showAppNotification,
    });

    const onMessage = (event: MessageEvent) => {
      const frameWindow = frameHostRef.current?.querySelector('iframe')?.contentWindow;
      if (!frameWindow || event.source !== frameWindow) {
        return;
      }
      void handle(event.data).then((reply) => {
        if (reply) {
          // The sandboxed page has an opaque origin, so '*' is the only target that reaches it.
          frameWindow.postMessage(reply, '*');
        }
      });
    };
    window.addEventListener('message', onMessage);
    return () => window.removeEventListener('message', onMessage);
  }, [artifactId, appName, versionSha256, frameHostRef]);
}
