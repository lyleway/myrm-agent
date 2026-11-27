/**
 * [INPUT]
 * - @/lib/api::apiRequest (POS: unified API request entry)
 * - @/lib/app-bridge (POS: capability vocabulary, grant record and host request/reply shapes)
 * - @/services/riskHeldToast::showRiskHeldRefusal (POS: the one notice for a text a risk rule holds)
 *
 * [OUTPUT]
 * - installApp / uninstallApp / listInstalledApps / fetchAppDetail: the install registry of HTML artifacts.
 * - saveGrant: store the user's decision on one capability.
 * - proxyAppFetch / proxyAppAi / reportAppActivity / fetchAppActivity: the host fetch and model-call proxies and the
 *   metadata-only activity log; a model call whose text a risk rule holds shows the shared notice and answers `blocked`;
 *   a report is sent with `keepalive`, so it reaches the server when the page closes right after the call.
 *
 * [POS]
 * HTTP client of the installed-app API (`/artifact-apps`). Only the app host page calls the proxy and the log:
 * a sandboxed page has no network or key, so the host sends its requests for it, and only for what the user allowed.
 */

import { apiRequest } from '@/lib/api';
import type { GrantRecord } from '@/lib/app-bridge/declaration';
import type {
  AppBridgeActivity,
  ProxyAiReply,
  ProxyAiRequest,
  ProxyFetchReply,
  ProxyFetchRequest,
} from '@/lib/app-bridge/hostTypes';
import type { BridgeCapability } from '@/lib/app-bridge/protocol';
import { showRiskHeldRefusal } from '@/services/riskHeldToast';

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

/** Just above the server's own 60 s limit on a model call, so the page sees the server's `timeout` answer. */
const AI_REQUEST_TIMEOUT_MS = 65_000;

/** What the page learns when a risk rule holds its text: the user is told by the shared notice, the page only that. */
const HELD_TEXT_REPLY: ProxyAiReply = {
  ok: false,
  text: '',
  code: 'blocked',
  message: "The text was held by the user's risk rules",
};

export async function proxyAppAi(artifactId: string, { maxTokens, ...request }: ProxyAiRequest): Promise<ProxyAiReply> {
  try {
    return await apiRequest<ProxyAiReply>(`/artifact-apps/${artifactId}/ai`, {
      method: 'POST',
      body: JSON.stringify({ ...request, max_tokens: maxTokens }),
      silent: true,
      timeout: AI_REQUEST_TIMEOUT_MS,
    });
  } catch (error) {
    if (error instanceof Error && showRiskHeldRefusal((error as { data?: unknown }).data)) {
      return HELD_TEXT_REPLY;
    }
    throw error;
  }
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
