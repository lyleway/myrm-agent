/**
 * [INPUT]
 * - @/lib/api::apiRequest (POS: unified API request entry)
 * - @/lib/app-bridge (POS: capability vocabulary, grant record and host request/reply shapes)
 *
 * [OUTPUT]
 * - installApp / uninstallApp / listInstalledApps / fetchAppDetail: the install registry of HTML artifacts.
 * - saveGrant: store the user's decision on one capability.
 * - proxyAppFetch / reportAppActivity / fetchAppActivity: the host fetch proxy and the metadata-only activity log.
 *
 * [POS]
 * HTTP client of the installed-app API (`/artifact-apps`). Only the app host page calls the proxy and the log:
 * a sandboxed page has no network, so the host sends its requests for it, and only for what the user allowed.
 */

import { apiRequest } from '@/lib/api';
import type { GrantRecord } from '@/lib/app-bridge/declaration';
import type { AppBridgeActivity, ProxyFetchReply, ProxyFetchRequest } from '@/lib/app-bridge/host';
import type { BridgeCapability } from '@/lib/app-bridge/protocol';

export interface InstalledApp {
  artifact_id: string;
  name: string;
  installed_at: string;
  updated_at: string;
}

export interface AppDetail {
  artifact_id: string;
  name: string;
  chat_id: string | null;
  latest_version: { id: string; vault_uri: string; sha256_hash: string };
  grants: GrantRecord[];
}

export interface AppActivity {
  capability: BridgeCapability;
  outcome: AppBridgeActivity['outcome'];
  detail: string | null;
  version_sha256: string;
  created_at: string;
}

export interface GrantDecision {
  decision: GrantRecord['decision'];
  scope: string[];
  version_sha256: string;
}

export function listInstalledApps(): Promise<InstalledApp[]> {
  return apiRequest<InstalledApp[]>('/artifact-apps', { silent: true });
}

export function installApp(artifactId: string): Promise<AppDetail> {
  return apiRequest<AppDetail>(`/artifact-apps/${artifactId}`, { method: 'PUT' });
}

export async function uninstallApp(artifactId: string): Promise<void> {
  await apiRequest(`/artifact-apps/${artifactId}`, { method: 'DELETE' });
}

export function fetchAppDetail(artifactId: string): Promise<AppDetail> {
  return apiRequest<AppDetail>(`/artifact-apps/${artifactId}`, { silent: true });
}

export function saveGrant(artifactId: string, capability: BridgeCapability, body: GrantDecision): Promise<GrantRecord> {
  return apiRequest<GrantRecord>(`/artifact-apps/${artifactId}/grants/${capability}`, {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

export function proxyAppFetch(artifactId: string, request: ProxyFetchRequest): Promise<ProxyFetchReply> {
  return apiRequest<ProxyFetchReply>(`/artifact-apps/${artifactId}/fetch`, {
    method: 'POST',
    body: JSON.stringify(request),
    silent: true,
  });
}

export async function reportAppActivity(
  artifactId: string,
  versionSha256: string,
  activity: AppBridgeActivity,
): Promise<void> {
  await apiRequest(`/artifact-apps/${artifactId}/activity`, {
    method: 'POST',
    body: JSON.stringify({ version_sha256: versionSha256, ...activity }),
    // The log is the user's record of what the app did; a request still on its way must outlive the closing page.
    keepalive: true,
    silent: true,
  });
}

export function fetchAppActivity(artifactId: string, limit: number): Promise<AppActivity[]> {
  return apiRequest<AppActivity[]>(`/artifact-apps/${artifactId}/activity?limit=${limit}`, { silent: true });
}
