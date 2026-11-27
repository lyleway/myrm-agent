/**
 * [INPUT]
 * - `@/lib/deploy-mode` (`getApiBaseUrl`) (POS: the API base address of the current deploy mode)
 * - `@/lib/backend-health` (`markLocalBackendUnreachable`) (POS: tells the app the local backend is down)
 * - `@/lib/platform-readiness` (`whenDatabaseReady`) (POS: whether the local database is up)
 * - `./BaseAdapter` BaseConfigAdapter / BACKEND_NOT_AVAILABLE_ERROR / backendUnavailableSyncResult / isTransportFailure / fetchWithDeadlines / isLostAnswer / DeadlineInit / DeadlineAnswer / ReadJson (POS: the failure answer and the deadline-bound request both adapters share)
 * - `../types` ConfigSyncError / record, change and sync-result shapes (POS: adapter contract)
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
  fetchWithDeadlines,
  isLostAnswer,
  isTransportFailure,
  type DeadlineAnswer,
  type DeadlineInit,
  type ReadJson,
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
   * 响应头与应答体各有期限，到期即取消请求（`fetchWithDeadlines`），后端卡死时这个请求既不无限等待，也不占着连接。
   * 请求前的就绪门（`whenDatabaseReady`）不在这两道期限内：门里探测后端的 `fetch`（`/health`、`/health/ready`）没有自己的
   * 期限，后端接收连接却不应答时，重试停在门口，直到后端重新应答；桌面壳里 `/health` 例外，它经 Tauri 命令探测，由 Rust
   * 侧限时 2 秒，门按尝试次数放弃，放弃后按后端不可用处理。
   */
  private async localFetch(url: string, init?: DeadlineInit): Promise<DeadlineAnswer> {
    const ready = await whenDatabaseReady();
    if (!ready) {
      throw new BackendUnavailableError();
    }

    let answer: DeadlineAnswer;
    try {
      answer = await fetchWithDeadlines(url, {
        ...init,
        credentials: 'include',
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
    }

    if (this.isBackendUnavailableStatus(answer.response.status)) {
      markLocalBackendUnreachable();
    }
    return answer;
  }

  private isBackendUnavailableStatus(status: number): boolean {
    return BACKEND_UNAVAILABLE_HTTP_STATUSES.has(status);
  }

  /**
   * The JSON body of an answer within its deadline. A body that never arrived (stalled, or the connection broke while it
   * was read) means the backend is down, like a failed request; one that arrived but is not JSON is a defect and stays
   * visible.
   */
  private async readBody<T>(readJson: ReadJson): Promise<T> {
    try {
      return await readJson<T>();
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
      const { response, readJson } = await this.localFetch(url);

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

      const data = await this.readBody<ApiAllConfigsResponse>(readJson);
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
      const { response, readJson } = await this.localFetch(`${this.baseUrl}/config/${key}`);

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

      const data = await this.readBody<ApiConfigResponse<K>>(readJson);
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
      const { response, readJson } = await this.localFetch(`${this.baseUrl}/config/sync`, {
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

      const data = await this.readBody<ApiSyncResponse>(readJson);

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
