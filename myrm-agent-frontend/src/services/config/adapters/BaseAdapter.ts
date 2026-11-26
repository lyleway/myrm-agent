/**
 * [INPUT]
 * - `../types` ConfigAdapter / SyncResult (POS: adapter contract and the backend's answer to a sync)
 *
 * [OUTPUT]
 * BaseConfigAdapter: device id and the abstract adapter surface.
 * BACKEND_NOT_AVAILABLE_ERROR / backendUnavailableSyncResult: the failed `SyncResult` of a write that never reached a backend.
 * isTransportFailure: whether a rejected `fetch()` call is a delivery failure rather than a bug.
 * fetchWithDeadlines / RESPONSE_HEADERS_DEADLINE_MS / RESPONSE_BODY_DEADLINE_MS: the request call of both adapters; a request whose headers or body outlast their deadline is cancelled.
 * isLostAnswer: whether a failed body read means the answer was lost rather than wrong.
 *
 * [POS]
 * What the Tauri and Sandbox adapters share. Both answer a reachability failure the same way, because the sync layer
 * keeps a write for a later retry only when it sees this result: reads (`getAll` / `get`) throw, so "unknown" is never
 * mistaken for "no such row", and `sync` returns the failed result instead of throwing. Reachability covers the body of
 * an answer as well as the request: a body that never finishes or breaks off counts as an unreachable backend, a body
 * that is not JSON does not. Whatever else a backend refuses is an error the caller sees.
 */

import type { ConfigAdapter, ConfigKey, ConfigRecord, ConfigChange, SyncResult, ConfigVersion } from '../types';

/** `SyncResult.error` of a write that did not reach a backend that could take it; `isRecoverableSyncError` matches it. */
export const BACKEND_NOT_AVAILABLE_ERROR = 'Backend not available';

export function backendUnavailableSyncResult(): SyncResult {
  return {
    success: false,
    conflicts: [],
    newVersions: new Map<ConfigKey, ConfigVersion>(),
    error: BACKEND_NOT_AVAILABLE_ERROR,
  };
}

/**
 * A `fetch()` that produced no response: offline, DNS or TLS failure (a `TypeError` in every browser, whatever its
 * wording) or the abort of the headers deadline of `fetchWithDeadlines`. Apply it to the `fetch()` call only; a
 * `TypeError` from handling the answer is a bug and must stay visible.
 */
export function isTransportFailure(error: unknown): boolean {
  return error instanceof TypeError || (error instanceof DOMException && error.name === 'AbortError');
}

/** How long a backend may take to start answering: the time until the headers of the answer arrive. */
export const RESPONSE_HEADERS_DEADLINE_MS = 10_000;

/**
 * How long the body of an answer may take once its headers have arrived. Equal to the default of the app's generic
 * request layer (`fetchWithTimeout`).
 */
export const RESPONSE_BODY_DEADLINE_MS = 30_000;

/** Request options of `fetchWithDeadlines`: everything `fetch()` takes except the signal, which the deadlines own. */
export type DeadlineInit = Omit<RequestInit, 'signal'>;

/** Reads the JSON body of an answer; the body deadline stops counting once the read has settled. */
export type ReadJson = <T>() => Promise<T>;

/** An answer whose headers have arrived, with the call that reads its JSON body within the body deadline. */
export interface DeadlineAnswer {
  response: Response;
  readJson: ReadJson;
}

/**
 * `fetch()` with a deadline on the headers and a later one on the body, both enforced by cancelling the request. An
 * answer that is only abandoned keeps its connection, and a browser allows few of them per origin (six over HTTP/1.1):
 * a backend that stalls again and again would starve every other request of the page, while a cancelled request frees
 * its connection. The body deadline starts when the headers arrive and also cancels a body nobody reads (an error
 * status the caller only inspects), which would hold its connection just the same.
 */
export async function fetchWithDeadlines(url: string, init: DeadlineInit): Promise<DeadlineAnswer> {
  const controller = new AbortController();
  const abortIn = (ms: number) => setTimeout(() => controller.abort(), ms);

  const headersTimer = abortIn(RESPONSE_HEADERS_DEADLINE_MS);
  let response: Response;
  try {
    response = await fetch(url, { ...init, signal: controller.signal });
  } finally {
    clearTimeout(headersTimer);
  }

  const bodyTimer = abortIn(RESPONSE_BODY_DEADLINE_MS);
  return {
    response,
    readJson: <T>() => (response.json() as Promise<T>).finally(() => clearTimeout(bodyTimer)),
  };
}

/**
 * Whether a failed `readJson` means the answer never made it (it stalled past the deadline, or the connection broke while
 * it was read) rather than that it arrived wrong. A body that is not JSON (`SyntaxError`) is a server defect and stays
 * visible.
 */
export function isLostAnswer(error: unknown): boolean {
  return !(error instanceof SyntaxError);
}

/**
 * 适配器基类：持有设备 ID，并声明子类必须实现的读取与批量同步。
 */
export abstract class BaseConfigAdapter implements ConfigAdapter {
  protected deviceId: string;

  constructor() {
    this.deviceId = this.initDeviceId();
  }

  /**
   * 初始化设备 ID
   * 子类可重写以提供不同的设备 ID 策略
   */
  protected initDeviceId(): string {
    if (typeof window === 'undefined') {
      return 'server';
    }

    let id = localStorage.getItem('config-device-id');
    if (!id) {
      id = crypto.randomUUID();
      localStorage.setItem('config-device-id', id);
    }
    return id;
  }

  /**
   * 获取设备 ID
   */
  getDeviceId(): string {
    return this.deviceId;
  }

  // ============================================================================
  // 抽象方法 - 子类必须实现
  // ============================================================================

  abstract getAll(keys?: readonly ConfigKey[]): Promise<Map<ConfigKey, ConfigRecord>>;

  abstract get<K extends ConfigKey>(key: K): Promise<ConfigRecord<K> | null>;

  abstract sync(changes: ConfigChange[]): Promise<SyncResult>;
}
