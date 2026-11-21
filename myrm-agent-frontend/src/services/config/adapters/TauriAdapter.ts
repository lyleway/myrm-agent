/**
 * [INPUT]
 * - `@/lib/deploy-mode` (`getApiBaseUrl`)
 * - `@/lib/backend-health` (`markLocalBackendUnreachable`) (POS: tells the app the local backend is down)
 * - `@/lib/platform-readiness` (`whenDatabaseReady`) (POS: whether the local database is up)
 * - `./BaseAdapter` BaseConfigAdapter / BACKEND_NOT_AVAILABLE_ERROR / backendUnavailableSyncResult / isTransportFailure / readJson / isLostAnswer (POS: the failure answer and the answer-body limit both adapters share)
 *
 * [OUTPUT]
 * - `TauriConfigAdapter`: local/Tauri 配置 HTTP 适配（含 Next 代理 5xx → offline 语义）
 *
 * [POS]
 * 本地模式配置同步适配器。WebUI 经 `/api/v1` 代理；Tauri 可直连 sidecar。与 `ConfigSyncManager` 离线队列配合。
 * 后端不可用（Next 代理 500/502/503/504、请求失败、超时、数据库未就绪、应答体迟迟读不完或读到一半断开）时：
 * `getAll` / `get` 抛错，不把「不知道」答成「没有这一行」；`sync` 返回 `backendUnavailableSyncResult()` 供同步层
 * 保留重试。请求失败只在 `fetch()` 调用处判定，应答体读不完只在读取处判定（`readBody`）；读到的应答体不是合法 JSON，
 * 或处理应答时出现 `TypeError`，都属于缺陷，照常抛出。
 */

import { markLocalBackendUnreachable } from '@/lib/backend-health';
import { getApiBaseUrl } from '@/lib/deploy-mode';
import { whenDatabaseReady } from '@/lib/platform-readiness';
import {
  BACKEND_NOT_AVAILABLE_ERROR,
  BaseConfigAdapter,
  backendUnavailableSyncResult,
  isLostAnswer,
  isTransportFailure,
  readJson,
} from './BaseAdapter';
import type { ConfigKey, ConfigRecord, ConfigChange, SyncResult, ConfigVersion, ConfigValueMap } from '../types';
import { ConfigSyncError } from '../types';

/**
 * 后端 API 响应类型
 */
interface ApiConfigResponse<K extends ConfigKey = ConfigKey> {
  key: K;
  value: ConfigValueMap[K];
  version: ConfigVersion;
  updatedAt: string;
  deviceId: string;
  isSystemDefault?: boolean;
  unreadable?: boolean;
}

interface ApiAllConfigsResponse {
  configs: Record<string, ApiConfigResponse>;
}

interface ApiSyncResponse {
  success: boolean;
  conflicts: ConfigKey[];
  newVersions: Record<string, ConfigVersion>;
  error?: string;
}

/** Next.js rewrite proxy and gateway errors when local backend is down. */
const BACKEND_UNAVAILABLE_HTTP_STATUSES = new Set([500, 502, 503, 504]);

/** The local backend could not answer: database not ready, request failed or timed out, or the answer body never arrived. */
class BackendUnavailableError extends Error {
  constructor() {
    super(BACKEND_NOT_AVAILABLE_ERROR);
    this.name = 'BackendUnavailableError';
  }
}

/**
 * Tauri 配置适配器
 */
export class TauriConfigAdapter extends BaseConfigAdapter {
  private get baseUrl(): string {
    return getApiBaseUrl();
  }

  protected override initDeviceId(): string {
    return 'tauri-local';
  }

  /**
   * 统一 fetch，自动携带 Cookie 以兼容 WebUI Remote 模式的 JWT 认证。
   * 非 Remote 模式下后端跳过认证，Cookie 虽携带但无影响。
   * 内置 10s 超时（计到响应头）防止后端卡死时前端无限等待；应答体另有期限，见 `readBody`。
   */
  private async localFetch(url: string, init?: RequestInit): Promise<Response> {
    const ready = await whenDatabaseReady();
    if (!ready) {
      throw new BackendUnavailableError();
    }

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10_000);

    let response: Response;
    try {
      response = await fetch(url, {
        ...init,
        credentials: 'include',
        signal: controller.signal,
        headers: {
          'Content-Type': 'application/json',
          ...init?.headers,
        },
      });
    } catch (error) {
      if (isTransportFailure(error)) {
        markLocalBackendUnreachable();
        throw new BackendUnavailableError();
      }
      throw error;
    } finally {
      clearTimeout(timeoutId);
    }

    if (this.isBackendUnavailableStatus(response.status)) {
      markLocalBackendUnreachable();
    }
    return response;
  }

  private isBackendUnavailableStatus(status: number): boolean {
    return BACKEND_UNAVAILABLE_HTTP_STATUSES.has(status);
  }

  /**
   * The JSON body of an answer within its deadline. A body that never arrived (stalled, or the connection broke while it
   * was read) means the backend is down, like a failed request; one that arrived but is not JSON is a defect and stays
   * visible.
   */
  private async readBody<T>(response: Response): Promise<T> {
    try {
      return await readJson<T>(response);
    } catch (error) {
      if (!isLostAnswer(error)) {
        throw error;
      }
      markLocalBackendUnreachable();
      throw new BackendUnavailableError();
    }
  }

  /**
   * 获取配置
   */
  async getAll(keys?: readonly ConfigKey[]): Promise<Map<ConfigKey, ConfigRecord>> {
    try {
      const url = keys?.length
        ? `${this.baseUrl}/config?keys=${encodeURIComponent(keys.join(','))}`
        : `${this.baseUrl}/config`;
      const response = await this.localFetch(url);

      if (!response.ok) {
        if (response.status === 404) {
          return new Map();
        }
        if (this.isBackendUnavailableStatus(response.status)) {
          console.warn('[TauriAdapter] Backend not available, will retry via ConfigSyncManager');
          throw new ConfigSyncError(BACKEND_NOT_AVAILABLE_ERROR);
        }
        throw new ConfigSyncError(`Failed to get all configs: ${response.statusText}`);
      }

      const data = await this.readBody<ApiAllConfigsResponse>(response);
      const result = new Map<ConfigKey, ConfigRecord>();

      for (const [key, record] of Object.entries(data.configs)) {
        result.set(key as ConfigKey, this.toConfigRecord(record as ApiConfigResponse));
      }

      return result;
    } catch (error) {
      if (error instanceof BackendUnavailableError) {
        console.warn('[TauriAdapter] Backend not available, will retry via ConfigSyncManager');
        throw new ConfigSyncError(BACKEND_NOT_AVAILABLE_ERROR);
      }
      throw error;
    }
  }

  /**
   * 获取单个配置
   */
  async get<K extends ConfigKey>(key: K): Promise<ConfigRecord<K> | null> {
    try {
      const response = await this.localFetch(`${this.baseUrl}/config/${key}`);

      if (response.status === 404) {
        return null;
      }

      if (!response.ok) {
        if (this.isBackendUnavailableStatus(response.status)) {
          console.warn(`[TauriAdapter] Backend not available, cannot read '${key}'`);
          throw new ConfigSyncError(BACKEND_NOT_AVAILABLE_ERROR);
        }
        throw new ConfigSyncError(`Failed to get config '${key}': ${response.statusText}`);
      }

      const data = await this.readBody<ApiConfigResponse<K>>(response);
      return this.toConfigRecord(data);
    } catch (error) {
      if (error instanceof BackendUnavailableError) {
        console.warn(`[TauriAdapter] Backend not available, cannot read '${key}'`);
        throw new ConfigSyncError(BACKEND_NOT_AVAILABLE_ERROR);
      }
      throw error;
    }
  }

  /**
   * 批量同步
   */
  async sync(changes: ConfigChange[]): Promise<SyncResult> {
    if (changes.length === 0) {
      return {
        success: true,
        conflicts: [],
        newVersions: new Map(),
      };
    }

    try {
      const response = await this.localFetch(`${this.baseUrl}/config/sync`, {
        method: 'POST',
        body: JSON.stringify({
          changes,
          deviceId: this.deviceId,
        }),
      });

      if (!response.ok) {
        if (this.isBackendUnavailableStatus(response.status)) {
          console.warn('[TauriAdapter] Backend not available, sync failed');
          return backendUnavailableSyncResult();
        }
        throw new ConfigSyncError(`Failed to sync configs: ${response.statusText}`);
      }

      const data = await this.readBody<ApiSyncResponse>(response);

      return {
        success: data.success,
        conflicts: data.conflicts,
        newVersions: new Map(Object.entries(data.newVersions)) as Map<ConfigKey, string>,
        error: data.error,
      };
    } catch (error) {
      if (error instanceof BackendUnavailableError) {
        console.warn('[TauriAdapter] Backend not available, sync failed');
        return backendUnavailableSyncResult();
      }
      throw error;
    }
  }

  /**
   * 转换 API 响应为 ConfigRecord
   */
  private toConfigRecord<K extends ConfigKey>(data: ApiConfigResponse<K>): ConfigRecord<K> {
    return {
      key: data.key,
      value: data.value,
      meta: {
        version: data.version,
        updatedAt: data.updatedAt,
        deviceId: data.deviceId,
      },
      isSystemDefault: data.isSystemDefault,
      unreadable: data.unreadable,
    };
  }
}
