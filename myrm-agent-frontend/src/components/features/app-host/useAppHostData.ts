/**
 * [INPUT]
 * - @/services/artifactApps (POS: HTTP client of the installed-app API)
 * - @/lib/app-bridge (POS: declaration parsing, permission status, external scripts, page-side client injection)
 * - @/lib/api::getStorageUrl (POS: resolves `vault://` references to a fetchable address)
 *
 * [OUTPUT]
 * - useAppHostData: loads an installed app (record, page HTML, decisions) and derives what the page declared and may use,
 *   and which other sites serve its scripts.
 *
 * [POS]
 * Data half of the app host page. The page HTML is the version recorded in the same response, so the declaration the
 * user reviews is the declaration of the code that runs. `html` is already prepared for the iframe (client injected).
 */

import { useCallback, useEffect, useMemo, useState } from 'react';
import { ApiError, getStorageUrl } from '@/lib/api';
import {
  EMPTY_DECLARATION,
  parseDeclaration,
  resolveStatuses,
  type CapabilityStatus,
  type Declaration,
  type GrantRecord,
} from '@/lib/app-bridge/declaration';
import { listExternalScripts, type ExternalScript } from '@/lib/app-bridge/externalScripts';
import type { BridgeCapability } from '@/lib/app-bridge/protocol';
import { injectAppBridge } from '@/lib/app-bridge/shim';
import { fetchAppDetail, type AppDetail } from '@/services/artifactApps';

export type AppLoadState = 'loading' | 'ready' | 'not_installed' | 'failed';

interface LoadedApp {
  detail: AppDetail;
  source: string;
  grants: GrantRecord[];
}

/** Outcome of loading one app; tagged with the id so a result is never shown for another app. */
interface LoadResult {
  artifactId: string;
  failure: 'not_installed' | 'failed' | null;
  app: LoadedApp | null;
}

export interface AppHostData {
  state: AppLoadState;
  detail: AppDetail | null;
  /** Page HTML with the client injected, ready for the iframe. */
  html: string | null;
  declaration: Declaration;
  statuses: Record<BridgeCapability, CapabilityStatus>;
  externalScripts: ExternalScript[];
  /** Merges decisions the user just saved into what the page may use. */
  applyGrants: (saved: GrantRecord[]) => void;
}

const NO_GRANTS: GrantRecord[] = [];

export function useAppHostData(artifactId: string): AppHostData {
  const [result, setResult] = useState<LoadResult | null>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async (): Promise<LoadResult> => {
      try {
        const detail = await fetchAppDetail(artifactId);
        const response = await fetch(getStorageUrl(detail.latest_version.vault_uri));
        if (!response.ok) {
          throw new Error('page unavailable');
        }
        const source = await response.text();
        return { artifactId, failure: null, app: { detail, source, grants: detail.grants } };
      } catch (error) {
        const failure = error instanceof ApiError && error.code === 404 ? 'not_installed' : 'failed';
        return { artifactId, failure, app: null };
      }
    };
    void load().then((loaded) => {
      if (!cancelled) {
        setResult(loaded);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [artifactId]);

  const current = result?.artifactId === artifactId ? result : null;
  const app = current?.app ?? null;
  const state: AppLoadState = current === null ? 'loading' : (current.failure ?? 'ready');

  const source = app?.source ?? null;
  const declaration = useMemo(() => (source === null ? EMPTY_DECLARATION : parseDeclaration(source)), [source]);
  const statuses = useMemo(() => resolveStatuses(declaration, app?.grants ?? NO_GRANTS), [declaration, app?.grants]);
  const html = useMemo(() => (source === null ? null : injectAppBridge(source)), [source]);
  const externalScripts = useMemo(() => (source === null ? [] : listExternalScripts(source)), [source]);

  const applyGrants = useCallback(
    (saved: GrantRecord[]) => {
      setResult((previous) => {
        if (previous?.artifactId !== artifactId || !previous.app) {
          return previous;
        }
        const grants = [
          ...previous.app.grants.filter((grant) => !saved.some((next) => next.capability === grant.capability)),
          ...saved,
        ];
        return { ...previous, app: { ...previous.app, grants } };
      });
    },
    [artifactId],
  );

  return { state, detail: app?.detail ?? null, html, declaration, statuses, externalScripts, applyGrants };
}
