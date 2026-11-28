import type { MemoryReindexResult } from '@/services/memory/reindex';

export type ApplyStatus = 'idle' | 'applying' | 'success' | 'error';

export interface ProviderModelConfig {
  provider: string;
  model: string;
  apiKey: string;
  apiBase?: string;
}

export interface RetrievalConfig {
  // Embedding 配置（临时编辑状态）
  embeddingConfig: ProviderModelConfig;
  embeddingApplied: boolean;
  embeddingAppliedAt?: number;
  embeddingApplyStatus: ApplyStatus;
  embeddingApplyMessage?: string;

  // Reranker 配置（临时编辑状态）
  rerankerConfig: ProviderModelConfig;
  rerankerApplied: boolean;
  rerankerAppliedAt?: number;
  rerankerApplyStatus: ApplyStatus;
  rerankerApplyMessage?: string;

  // 重排序开关（仅控制记忆召回的重排序；字段名随已持久化的配置与后端请求字段保持不变）
  enableAdvancedRetrieval: boolean;

  // Orphan collection 状态（模型切换后的旧记忆检测）
  orphanCount: number;

  // 旧记忆迁移运行状态：'running' 期间轮询服务端后台任务，'done' 带逐条计数，'error' 表示运行中断
  reindexStatus: ReindexRunStatus;
  reindexProgress: { done: number; total: number };
  reindexResult: MemoryReindexResult | null;
}

export type ReindexRunStatus = 'idle' | 'running' | 'done' | 'error';

interface RetrievalActions {
  // 初始化
  initRetrieval: () => Promise<void>;

  // Embedding 配置
  setEmbeddingConfig: (config: ProviderModelConfig) => void;
  applyEmbedding: () => Promise<{ success: boolean; message: string }>;
  resetEmbeddingToApplied: () => void;

  // Reranker 配置
  setRerankerConfig: (config: ProviderModelConfig) => void;
  applyReranker: () => Promise<{ success: boolean; message: string }>;
  resetRerankerToApplied: () => void;

  // 重排序开关
  setEnableAdvancedRetrieval: (enable: boolean) => void;

  // Orphan 重建
  checkOrphanCollections: () => Promise<void>;
  executeReindex: () => Promise<void>;
  dismissOrphanWarning: () => void;

  // 重置所有配置
  reset: () => void;
}

export type RetrievalStore = RetrievalConfig & RetrievalActions & { isInitialized: boolean };
