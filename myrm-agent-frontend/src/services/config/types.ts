/**
 * [INPUT]
 * - `./configValues` ConfigValueMap and the per-key value shapes (POS: what each config key stores)
 * - `./configPolicyValues` (POS: the security, budget, backup, browser and external-agent value shapes)
 *
 * [OUTPUT]
 * `ConfigKey` with its key sets, the record and version types with the version helpers, the change and sync contract
 * (`ConfigChange`, `SyncResult`, `SyncStatus`, `ConfigAdapter`), `ConfigSyncError`, the `DEFAULT_*` values, and every
 * value shape of the two value modules, re-exported.
 *
 * [POS]
 * Type entry of the config sync layer: optimistic-lock versions and multi-device sync are built on the keys and the
 * value map defined here. The value shapes live in two files by theme; importers use this module as the single entry.
 */

import type {
  ConfigValueMap,
  MCPServersConfigValue,
  PersonalSettingsConfigValue,
  SearchServicesConfigValue,
} from './configValues';

export type * from './configValues';
export type * from './configPolicyValues';

// ============================================================================
// 配置键定义
// ============================================================================

/**
 * 配置键类型
 *
 * 每个键对应一类配置数据：
 * - providers: 模型服务商配置（敏感）
 * - chatSettings: 聊天设置
 * - personalSettings: 个人偏好设置
 * - privacyRoutingSecret: 本地模型端点的密钥（敏感，不放进 personalSettings）
 * - mcpServers: MCP 服务器配置（敏感）
 * - searchServices: 搜索服务配置（敏感）
 * - commands: 用户自定义命令
 * - retrieval: 检索配置（敏感）
 * - channels: 通知渠道配置
 * - voice: 语音配置
 * - securityConfig: 安全策略配置
 * - *Credentials: 各平台凭证配置（敏感）
 */
export type ConfigKey =
  | 'providers'
  | 'chatSettings'
  | 'personalSettings'
  | 'privacyRoutingSecret'
  | 'mcpServers'
  | 'searchServices'
  | 'commands'
  | 'retrieval'
  | 'channels'
  | 'voice'
  | 'securityConfig'
  | 'externalAgents'
  | 'feishuCredentials'
  | 'dingtalkCredentials'
  | 'slackCredentials'
  | 'discordCredentials'
  | 'wecomCredentials'
  | 'wechatCredentials'
  | 'teamsCredentials'
  | 'matrixCredentials'
  | 'telegramCredentials'
  | 'googlechatCredentials'
  | 'budget_policy'
  | 'backupSync'
  | 'securityDashboardSettings'
  | 'browserCloudProvider'
  | 'browserProxy'
  | 'captchaSolverConfig'
  | 'webFetchEscalation'
  | 'orgMcpServers';

/** 所有配置键（用于按需加载） */
export const ALL_CONFIG_KEYS: readonly ConfigKey[] = [
  'providers',
  'chatSettings',
  'personalSettings',
  'privacyRoutingSecret',
  'mcpServers',
  'searchServices',
  'commands',
  'retrieval',
  'channels',
  'voice',
  'securityConfig',
  'externalAgents',
  'feishuCredentials',
  'dingtalkCredentials',
  'slackCredentials',
  'discordCredentials',
  'wecomCredentials',
  'wechatCredentials',
  'teamsCredentials',
  'matrixCredentials',
  'telegramCredentials',
  'googlechatCredentials',
  'budget_policy',
  'backupSync',
  'securityDashboardSettings',
  'browserCloudProvider',
  'browserProxy',
  'captchaSolverConfig',
  'webFetchEscalation',
  'orgMcpServers',
] as const;

/** 首屏核心配置（优先加载以加快启动） */
export const CORE_CONFIG_KEYS: readonly ConfigKey[] = ['providers', 'chatSettings', 'personalSettings'] as const;

// ============================================================================
// 配置元数据
// ============================================================================

/**
 * 配置版本号
 *
 * 格式：timestamp_counter
 * - timestamp: Unix 毫秒时间戳
 * - counter: 同一毫秒内的递增计数器
 *
 * 示例：1706000000000_0, 1706000000000_1
 */
export type ConfigVersion = string;

/**
 * 配置元数据
 */
export interface ConfigMeta {
  /** 版本号（时间戳_计数器格式） */
  version: ConfigVersion;
  /** 最后修改时间 (ISO 8601) */
  updatedAt: string;
  /** 最后修改的设备 ID */
  deviceId: string;
}

/**
 * 完整配置记录
 */
export interface ConfigRecord<K extends ConfigKey = ConfigKey> {
  key: K;
  value: ConfigValueMap[K];
  meta: ConfigMeta;
  /** 是否服务端加密存储（只读标志，由后端自动管理） */
  encrypted?: boolean;
  /** 该配置来自 __system__ 默认配置，用户尚未自定义 */
  isSystemDefault?: boolean;
  /** 已加密存储但封存它的密钥已丢失：value 为空，不是「用户没配置」，不能用默认值覆盖回写 */
  unreadable?: boolean;
}

// ============================================================================
// 版本号工具函数
// ============================================================================

/**
 * 创建初始版本号
 */
export function createInitialVersion(): ConfigVersion {
  return `${Date.now()}_0`;
}

/**
 * 递增版本号
 */
export function incrementVersion(current: ConfigVersion): ConfigVersion {
  const now = Date.now();
  const [timestamp, counter] = current.split('_').map(Number);

  if (timestamp === now) {
    return `${now}_${counter + 1}`;
  }
  return `${now}_0`;
}

// ============================================================================
// 同步相关类型
// ============================================================================

/**
 * 配置变更记录
 */
export interface ConfigChange<K extends ConfigKey = ConfigKey> {
  key: K;
  /** 完整的新值 */
  value: ConfigValueMap[K];
  /** 期望的服务端版本（乐观锁） */
  expectedVersion: ConfigVersion;
  /** 变更时间戳 */
  timestamp: number;
}

/**
 * 同步结果
 */
export interface SyncResult {
  /** 是否成功 */
  success: boolean;
  /** 版本冲突的配置键 */
  conflicts: ConfigKey[];
  /** 更新后的版本号 */
  newVersions: Map<ConfigKey, ConfigVersion>;
  /** 错误信息（如果有） */
  error?: string;
}

/** 同步管理器的运行状态 */
export type SyncStatus = 'idle' | 'loading' | 'syncing' | 'error' | 'offline';

// ============================================================================
// 适配器接口
// ============================================================================

/**
 * 配置适配器接口
 *
 * 所有部署模式（Tauri/Sandbox）都实现此接口
 */
export interface ConfigAdapter {
  /**
   * 获取配置
   * @param keys 可选，只加载指定键，减少传输量（Sandbox 模式优化）
   */
  getAll(keys?: readonly ConfigKey[]): Promise<Map<ConfigKey, ConfigRecord>>;

  /**
   * 获取单个配置
   */
  get<K extends ConfigKey>(key: K): Promise<ConfigRecord<K> | null>;

  /**
   * 批量同步
   */
  sync(changes: ConfigChange[]): Promise<SyncResult>;
}

// ============================================================================
// 错误类型
// ============================================================================

/**
 * 配置同步错误
 */
export class ConfigSyncError extends Error {
  constructor(
    message: string,
    public readonly cause?: Error,
  ) {
    super(message);
    this.name = 'ConfigSyncError';
  }
}

// ============================================================================
// 默认值
// ============================================================================

/**
 * 默认的 PersonalSettings 配置
 */
export const DEFAULT_PERSONAL_SETTINGS: PersonalSettingsConfigValue = {
  systemInstructions: '',
  fetchRawWebpage: false,
  extractDocumentText: true,
  generateSearchSuggestions: true,
  enableCostEstimation: true,
  enableCacheBreakNotification: false,
  showContextUsage: true,
  reasoningDisplayMode: 'collapsed',
  enableMemory: true,
  memoryRequireConfirmation: true,
  enableMemoryAutoExtraction: true,
  memoryEnableConversationSearch: false,
  preCompactEnabled: true,
  preCompactBudgetTokens: 1500,
  enableAutoTitleGeneration: true,
  webTtsProvider: 'browser',
  timezone: '',
  enableWebNotifications: true,
  enableCompletionSound: true,
  enableIdleApprovalNotification: true,
  approvalNotificationSound: true,
  codeExecutionAllowNetwork: true,
  enableEvalLab: false,
  smoothStreamEnabled: true,
  suggestWorkflowMode: false,
  autoContinueInterruptedTurns: true,
  publicIngressBaseUrl: '',
  activeThemeProfileId: 'official-default',
  themeProfiles: [],
};

/**
 * 默认的 MCPServers 配置
 */
export const DEFAULT_MCP_SERVERS: MCPServersConfigValue = {
  mcpConfigs: [],
};

/**
 * 默认的 SearchServices 配置
 */
export const DEFAULT_SEARCH_SERVICES: SearchServicesConfigValue = {
  searchServiceConfigs: [],
};
