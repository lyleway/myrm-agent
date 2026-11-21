/**
 * [INPUT]
 * - `@/lib/utils/withTimeout` withTimeout (POS: the generic wait limit for a promise; a result that arrives after the limit is dropped)
 * - `../types` ConfigAdapter / SyncResult (POS: adapter contract and the backend's answer to a sync)
 *
 * [OUTPUT]
 * BaseConfigAdapter: device id and the abstract adapter surface.
 * BACKEND_NOT_AVAILABLE_ERROR / backendUnavailableSyncResult: the failed `SyncResult` of a write that never reached a backend.
 * isTransportFailure: whether a rejected `fetch()` call is a delivery failure rather than a bug.
 * RESPONSE_BODY_DEADLINE_MS / readJson / isLostAnswer: the limit on reading an answer's body, and whether a failed read means the answer was lost rather than wrong.
 *
 * [POS]
 * What the Tauri and Sandbox adapters share. Both answer a reachability failure the same way, because the sync layer
 * keeps a write for a later retry only when it sees this result: reads (`getAll` / `get`) throw, so "unknown" is never
 * mistaken for "no such row", and `sync` returns the failed result instead of throwing. Reachability covers the body of
 * an answer as well as the request: a body that never finishes or breaks off counts as an unreachable backend, a body
 * that is not JSON does not. Whatever else a backend refuses is an error the caller sees.
 */

import { withTimeout } from '@/lib/utils/withTimeout';
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
 * wording) or the adapter's own timeout abort. Apply it to the `fetch()` call only; a `TypeError` from handling the
 * answer is a bug and must stay visible.
 */
export function isTransportFailure(error: unknown): boolean {
  return error instanceof TypeError || (error instanceof DOMException && error.name === 'AbortError');
}

/**
 * How long the body of an answer may take once its headers have arrived. The adapters' own 10 s timer ends with the
 * headers, so without this limit a backend that stalls in the middle of an answer leaves the read pending forever.
 * Equal to the default of the app's generic request layer (`fetchWithTimeout`).
 */
export const RESPONSE_BODY_DEADLINE_MS = 30_000;

/**
 * The JSON body of an answer, read within `RESPONSE_BODY_DEADLINE_MS`. The read is abandoned, not cancelled: whatever the
 * backend sends after the deadline is dropped.
 */
export function readJson<T>(response: Response): Promise<T> {
  return withTimeout(response.json() as Promise<T>, RESPONSE_BODY_DEADLINE_MS);
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
