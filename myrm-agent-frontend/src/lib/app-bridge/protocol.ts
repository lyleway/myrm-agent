/**
 * [INPUT]
 * - (none)
 *
 * [OUTPUT]
 * - BridgeCapability / BridgeFailure: the capabilities a page can ask the host for and why a call can fail.
 * - DESKTOP_CAPABILITIES / isDesktopCapability: the capabilities that exist only in an app's own desktop window.
 * - RpcRequest / RpcResponse / parseRpcRequest / rpcResult / rpcFailure / rpcEvent: the JSON-RPC 2.0 envelope of the
 *   page-to-host channel and the host-to-page notification.
 * - BridgeError: thrown by host handlers; carries the stable `reason` the page sees.
 * - parseNotifyParams / parseFetchParams / parseTrayParams / parseShortcutParams / parseFloatParams: shape checks.
 *
 * [POS]
 * Wire contract between a sandboxed app page and the host that embeds it. A method is named after the capability it
 * needs (`tray.set` needs `tray`). The host re-validates everything; the page-side shim only builds well-formed
 * requests. The host talks back with notifications named `myrm/event` (a revoked permission, a pressed key combination).
 */

import { normalizeAccelerator } from './accelerator';

export type BridgeCapability = 'notify' | 'fetch' | 'tray' | 'shortcut' | 'float';

export const BRIDGE_CAPABILITIES: readonly BridgeCapability[] = ['notify', 'fetch', 'tray', 'shortcut', 'float'];

/** These act on the operating system around the app window, so they exist only in the app's own desktop window. */
export const DESKTOP_CAPABILITIES: readonly BridgeCapability[] = ['tray', 'shortcut', 'float'];

export function isDesktopCapability(capability: BridgeCapability): boolean {
  return DESKTOP_CAPABILITIES.includes(capability);
}

export const NOTIFY_TITLE_MAX = 80;
export const NOTIFY_BODY_MAX = 200;
export const FETCH_URL_MAX = 2048;
export const TRAY_TITLE_MAX = 24;
export const TRAY_TOOLTIP_MAX = 80;

/** Why a call failed, as the page sees it (`error.data.reason`; the shim exposes it as `error.code`). */
export type BridgeFailure =
  | 'method_not_found'
  | 'invalid_params'
  | 'not_declared'
  | 'permission_denied'
  | 'rate_limited'
  | 'host_not_granted'
  | 'unsupported'
  | 'shortcut_not_granted'
  | 'shortcut_conflict'
  | 'bad_request'
  | 'blocked'
  | 'response_too_large'
  | 'timeout'
  | 'upstream_error'
  | 'failed';

export type RpcId = number | string;

export interface RpcRequest {
  id: RpcId;
  method: string;
  params: unknown;
}

export type RpcResponse =
  | { jsonrpc: '2.0'; id: RpcId; result: unknown }
  | { jsonrpc: '2.0'; id: RpcId; error: { code: number; message: string; data: { reason: BridgeFailure } } };

const METHOD_NOT_FOUND = -32601;
const INVALID_PARAMS = -32602;
const APPLICATION_ERROR = -32000;

export class BridgeError extends Error {
  constructor(
    readonly reason: BridgeFailure,
    message: string,
  ) {
    super(message);
    this.name = 'BridgeError';
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/** Anything that is not a JSON-RPC 2.0 request carrying an id is not ours (theme, resize and storage messages share the channel). */
export function parseRpcRequest(data: unknown): RpcRequest | null {
  if (!isRecord(data) || data.jsonrpc !== '2.0' || typeof data.method !== 'string') {
    return null;
  }
  if (typeof data.id !== 'number' && typeof data.id !== 'string') {
    return null;
  }
  return { id: data.id, method: data.method, params: data.params };
}

export function rpcResult(id: RpcId, result: unknown): RpcResponse {
  return { jsonrpc: '2.0', id, result };
}

export function rpcFailure(id: RpcId, error: BridgeError): RpcResponse {
  const code =
    error.reason === 'method_not_found'
      ? METHOD_NOT_FOUND
      : error.reason === 'invalid_params'
        ? INVALID_PARAMS
        : APPLICATION_ERROR;
  return { jsonrpc: '2.0', id, error: { code, message: error.message, data: { reason: error.reason } } };
}

/** `myrm/event` notification: the host tells the page something happened (no reply is expected). */
export const EVENT_METHOD = 'myrm/event';

export type AppEvent =
  | { name: 'permission-revoked'; data: { capability: BridgeCapability } }
  | { name: 'shortcut'; data: { accelerator: string } };

export function rpcEvent(event: AppEvent): { jsonrpc: '2.0'; method: typeof EVENT_METHOD; params: AppEvent } {
  return { jsonrpc: '2.0', method: EVENT_METHOD, params: event };
}

export interface NotifyParams {
  title: string;
  body: string;
}

export function parseNotifyParams(params: unknown): NotifyParams | null {
  if (!isRecord(params) || typeof params.title !== 'string') {
    return null;
  }
  const title = params.title.trim();
  const body = params.body ?? '';
  if (!title || title.length > NOTIFY_TITLE_MAX || typeof body !== 'string' || body.length > NOTIFY_BODY_MAX) {
    return null;
  }
  return { title, body };
}

export type FetchMethod = 'GET' | 'HEAD' | 'POST';

export interface FetchParams {
  url: string;
  method: FetchMethod;
  headers: Record<string, string>;
  body: string | null;
}

const FETCH_METHODS: readonly string[] = ['GET', 'HEAD', 'POST'];

export function parseFetchParams(params: unknown): FetchParams | null {
  if (!isRecord(params) || typeof params.url !== 'string' || params.url.length > FETCH_URL_MAX) {
    return null;
  }
  const method = params.method ?? 'GET';
  const body = params.body ?? null;
  const headers = params.headers ?? {};
  if (typeof method !== 'string' || !FETCH_METHODS.includes(method) || (body !== null && typeof body !== 'string')) {
    return null;
  }
  if (!isRecord(headers) || !Object.values(headers).every((value) => typeof value === 'string')) {
    return null;
  }
  return { url: params.url, method: method as FetchMethod, headers: headers as Record<string, string>, body };
}

export interface TrayParams {
  title: string;
  tooltip: string;
}

/** At least one of `title` (menu-bar text on macOS) and `tooltip` must be given; each is trimmed and capped. */
export function parseTrayParams(params: unknown): TrayParams | null {
  if (!isRecord(params)) {
    return null;
  }
  const { title = '', tooltip = '' } = params;
  if (typeof title !== 'string' || typeof tooltip !== 'string') {
    return null;
  }
  const parsed = { title: title.trim(), tooltip: tooltip.trim() };
  if (parsed.title.length > TRAY_TITLE_MAX || parsed.tooltip.length > TRAY_TOOLTIP_MAX) {
    return null;
  }
  return parsed.title || parsed.tooltip ? parsed : null;
}

/** The canonical combination, or `null` when the params carry anything else. */
export function parseShortcutParams(params: unknown): string | null {
  if (!isRecord(params) || typeof params.accelerator !== 'string') {
    return null;
  }
  return normalizeAccelerator(params.accelerator);
}

export function parseFloatParams(params: unknown): boolean | null {
  return isRecord(params) && typeof params.on === 'boolean' ? params.on : null;
}
