/**
 * [INPUT]
 * - `@/store/config/providerTypes` / `@/store/config/types` / `@/services/channels` / `@/types/command` (POS: the
 *   provider, MCP, search, channel and command shapes that a config value embeds)
 * - `./configPolicyValues` (POS: the security, budget, backup, browser and external-agent value shapes)
 *
 * [OUTPUT]
 * The value shape of every config key (providers, chat and personal settings, MCP and search services, commands,
 * retrieval, channels, voice, channel credentials) and `ConfigValueMap`, which pairs each key with its shape.
 *
 * [POS]
 * Value-shape half of the config types: what a stored config value looks like. `./types` re-exports it and builds the
 * record, change and sync contract on `ConfigValueMap`.
 */

import type { ProviderConfig, DefaultModelConfig, CustomModelInfo } from '@/store/config/providerTypes';
import type { MCPServiceConfig, SearchServiceConfigItem } from '@/store/config/types';
import type {
  ChannelsConfig,
  DiscordCredentials,
  DingTalkCredentials,
  FeishuCredentials,
  GoogleChatCredentials,
  MatrixCredentials,
  SlackCredentials,
  TeamsCredentials,
  TelegramCredentials,
  WeComCredentials,
  WeChatCredentials,
} from '@/services/channels';
import type { SlashCommand } from '@/types/command';
import type {
  BackupSyncConfigValue,
  BrowserCloudProviderConfigValue,
  BrowserProxyConfigValue,
  BudgetPolicyConfigValue,
  CaptchaSolverConfigValue,
  ExternalAgentsConfigValue,
  SecurityConfigValue,
  SecurityDashboardSettingsConfigValue,
  WebFetchEscalationConfigValue,
} from './configPolicyValues';

/**
 * Providers 配置值
 */
export interface ProvidersConfigValue {
  providers: ProviderConfig[];
  defaultModelConfig: DefaultModelConfig;
  customModelInfo: Record<string, CustomModelInfo>;
}

/**
 * ChatSettings 配置值
 */
export interface ChatSettingsConfigValue {
  defaultModelConfig: DefaultModelConfig;
  customModelInfo: Record<string, CustomModelInfo>;
}

/**
 * PersonalSettings 配置值
 */
export type WebTtsProvider = 'browser' | 'openai' | 'elevenlabs' | 'fish_audio' | 'minimax' | 'edge';
export type ReasoningDisplayMode = 'off' | 'collapsed' | 'inline';

export interface NotificationDelivery {
  channel: string;
  target: string;
}

export type PIIAction = 'warn' | 'redact' | 'pseudonymize' | 'block';

export type PrivacyS2Strategy = 'cloud_after_redact' | 'local';
export type PrivacyS3Strategy = 'local' | 'block';
export type PrivacyLocalFallback = 'block' | 'force_redact_cloud';

export interface PrivacyRoutingConfig {
  localModel?: string;
  localBaseUrl?: string;
  s2Strategy?: PrivacyS2Strategy;
  s3Strategy?: PrivacyS3Strategy;
  localFallback?: PrivacyLocalFallback;
}

/** 本地模型端点的密钥：独立的加密配置行，不随偏好、审计历史或默认导出出现。 */
export interface PrivacyRoutingSecretConfigValue {
  localApiKey: string;
}

export type VideoGenerationProvider = 'gemini' | 'qwen' | 'minimax' | 'xai' | 'fal' | 'volcengine';

export interface ImageGenerationConfig {
  model: string;
  fallbackModels: string[];
  defaultSize: string;
  timeoutSeconds: number;
  maxRetries: number;
}

export interface VideoGenerationConfig {
  provider: VideoGenerationProvider;
  model: string;
  fallbackProviders: Array<{ provider: string; model: string }>;
  timeoutSeconds: number;
  maxRetries: number;
  defaultAspectRatio?: string;
  defaultResolution?: string;
  defaultDurationSeconds?: number;
}

export interface PersonalSettingsConfigValue {
  systemInstructions: string;
  fetchRawWebpage: boolean;
  extractDocumentText: boolean;
  generateSearchSuggestions: boolean;
  enableCostEstimation: boolean;
  enableCacheBreakNotification: boolean;
  showContextUsage: boolean;
  reasoningDisplayMode: ReasoningDisplayMode;
  enableMemory: boolean;
  memoryRequireConfirmation: boolean;
  enableMemoryAutoExtraction: boolean;
  memoryEnableConversationSearch: boolean;
  preCompactEnabled: boolean;
  preCompactBudgetTokens: number;
  enableAutoTitleGeneration: boolean;
  webTtsProvider: WebTtsProvider;
  timezone: string;
  locale?: string;
  activeThemeProfileId?: string;
  themeProfiles?: import('@/theme-engine/schema').ThemeProfileRecipe[];
  themeFontOverride?: import('@/lib/fonts').FontId;
  enableWebNotifications: boolean;
  enableCompletionSound: boolean;
  enableIdleApprovalNotification: boolean;
  approvalNotificationSound: boolean;
  notificationDeliveries?: NotificationDelivery[];
  privacyEnabled?: boolean;
  privacyS2Action?: PIIAction;
  privacyS3Action?: PIIAction;
  privacyDeepScan?: boolean;
  privacyRouting?: PrivacyRoutingConfig;
  privacyCustomKeywordsS2?: string[];
  privacyCustomKeywordsS3?: string[];
  privacyCustomPatternsS2?: string[];
  privacyCustomPatternsS3?: string[];
  privacySensitiveToolsS2?: string[];
  privacySensitiveToolsS3?: string[];
  imageGeneration?: ImageGenerationConfig;
  videoGeneration?: VideoGenerationConfig;
  codeExecutionAllowNetwork?: boolean;
  enableEvalLab?: boolean;
  smoothStreamEnabled?: boolean;
  suggestWorkflowMode?: boolean;
  autoContinueInterruptedTurns?: boolean;
  publicIngressBaseUrl?: string;
  enterpriseTlsCompat?: boolean;
}

/**
 * MCPServers 配置值
 */
export interface MCPServersConfigValue {
  mcpConfigs: MCPServiceConfig[];
}

export interface OrgMcpServersConfigValue {
  servers: MCPServiceConfig[];
}

/**
 * SearchServices 配置值
 */
export interface SearchServicesConfigValue {
  searchServiceConfigs: SearchServiceConfigItem[];
}

/**
 * Commands 配置值（用户自定义命令）
 */
export interface CommandsConfigValue {
  commands: SlashCommand[];
  recentCommandIds: string[];
}

/**
 * Retrieval Provider/Model 配置
 */
export interface RetrievalProviderModelConfig {
  provider: string;
  model: string;
  apiKey: string;
  apiBase?: string;
}

/**
 * Retrieval 配置值（Embedding/Reranker，敏感）
 */
export interface RetrievalConfigValue {
  embeddingConfig?: RetrievalProviderModelConfig;
  embeddingApplied?: boolean;
  embeddingAppliedAt?: number;
  rerankerConfig?: RetrievalProviderModelConfig;
  rerankerApplied?: boolean;
  rerankerAppliedAt?: number;
  enableAdvancedRetrieval?: boolean;
}

/**
 * 通知渠道配置值
 */
export type ChannelsConfigValue = ChannelsConfig;

/**
 * 语音配置值
 */
export interface VoiceConfigValue {
  sttEnabled: boolean;
  sttProvider: string;
  sttApiKey: string;
  sttModel: string;
  sttLanguage: string;
  sttLocalModel: string;
  sttLocalDevice: string;
  sttLocalComputeType: string;
  sttBaseUrl: string;
  ttsMode: string;
  ttsProvider: string;
  /** TTS model id for the agent tts_generate tool (server media param chain). */
  ttsModel?: string;
  ttsApiKey: string;
  ttsBaseUrl: string;
  ttsVoice: string;
  ttsMaxLength: number;
  ttsSummaryEnabled: boolean;
  ttsSummaryThreshold: number;
  ttsSummaryModel: string;
}

/**
 * Feishu 凭证配置值
 */
export type FeishuCredentialsConfigValue = FeishuCredentials;

/**
 * 配置键到值类型的映射
 */
export interface ConfigValueMap {
  providers: ProvidersConfigValue;
  defaultModelConfig: DefaultModelConfig;
  customModelInfo: Record<string, CustomModelInfo>;
  chatSettings: ChatSettingsConfigValue;
  personalSettings: PersonalSettingsConfigValue;
  privacyRoutingSecret: PrivacyRoutingSecretConfigValue;
  mcpServers: MCPServersConfigValue;
  searchServices: SearchServicesConfigValue;
  commands: CommandsConfigValue;
  retrieval: RetrievalConfigValue;
  channels: ChannelsConfigValue;
  voice: VoiceConfigValue;
  feishuCredentials: FeishuCredentialsConfigValue;
  dingtalkCredentials: DingTalkCredentials;
  slackCredentials: SlackCredentials;
  discordCredentials: DiscordCredentials;
  wecomCredentials: WeComCredentials;
  wechatCredentials: WeChatCredentials;
  teamsCredentials: TeamsCredentials;
  matrixCredentials: MatrixCredentials;
  telegramCredentials: TelegramCredentials;
  googlechatCredentials: GoogleChatCredentials;
  securityConfig: SecurityConfigValue;
  externalAgents: ExternalAgentsConfigValue;
  budget_policy: BudgetPolicyConfigValue;
  backupSync: BackupSyncConfigValue;
  securityDashboardSettings: SecurityDashboardSettingsConfigValue;
  browserCloudProvider: BrowserCloudProviderConfigValue;
  browserProxy: BrowserProxyConfigValue;
  captchaSolverConfig: CaptchaSolverConfigValue;
  webFetchEscalation: WebFetchEscalationConfigValue;
  orgMcpServers: OrgMcpServersConfigValue;
}
