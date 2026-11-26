/**
 * [INPUT]
 * - ../ArtifactCard (POS: the card under test is imported by the test files, after this module)
 *
 * [OUTPUT]
 * - env: chat store, content cache and fake backend routes the card talks to
 * - artifactOf, PUBLICATION, SECOND_PUBLICATION, PAGE_URL, PREFLIGHT_URL, HYDRATE_URL: fixtures
 * - fetchMock, storageFetches, deployable: the fake backend and what was asked of it
 * - installArtifactCardHarness: per-test reset
 *
 * [POS]
 * Shared stand-ins for the ArtifactCard tests: stores, requests and the card's collaborators (preview renderers, publish
 * modal, panels) are replaced by thin fakes so each assertion is about the card's own wiring. A test file must import
 * this module before the card so that its vi.mock calls take effect first.
 */

import { beforeEach, vi } from 'vitest';

interface FakeResponse {
  ok: boolean;
  json?: unknown;
  text?: string;
  /** The request fails before any response, as when the network is down. */
  throws?: boolean;
}

const hoisted = vi.hoisted(() => {
  const chat = {
    chatId: 'chat-1',
    workspaceDir: '/ws',
    agentConfig: { agentId: 'agent-7', agentName: 'Agent', presetName: 'Preset' },
    files: [] as unknown[],
    setFiles: (files: unknown[]) => {
      chat.files = files;
    },
    updateMessages: vi.fn(),
  };
  const cache = new Map<string, string>();
  return {
    chat,
    cache,
    cacheApi: {
      getCachedContent: (id: string) => cache.get(id) ?? null,
      setCachedContent: (id: string, content: string) => {
        cache.set(id, content);
      },
    },
    routes: new Map<string, FakeResponse>(),
    toast: { error: vi.fn(), success: vi.fn(), info: vi.fn() },
    ingestArtifact: vi.fn(),
    writeToClipboard: vi.fn(),
    tryMarkMilestone: vi.fn(),
  };
});

export const env = hoisted;

const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('@/lib/api', () => ({
  apiRequest: vi.fn().mockResolvedValue({}),
  getApiUrl: (path: string) => `api:${path}`,
  getStorageUrl: (url: string) => `storage:${url}`,
}));
vi.mock('@/store/useChatStore', () => {
  const hook = (selector: (state: typeof hoisted.chat) => unknown) => selector(hoisted.chat);
  return { default: Object.assign(hook, { getState: () => hoisted.chat }) };
});
vi.mock('@/store/useArtifactPortalStore', () => ({ default: () => hoisted.cacheApi }));
vi.mock('sonner', () => ({ toast: hoisted.toast }));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: () => false }));
vi.mock('@/lib/utils/clipboardUtils', () => ({ writeToClipboard: hoisted.writeToClipboard }));
vi.mock('@/lib/progression/tryMarkMilestone', () => ({ tryMarkMilestone: hoisted.tryMarkMilestone }));
vi.mock('@/services/wikiService', () => ({ wikiService: { ingestArtifact: hoisted.ingestArtifact } }));
vi.mock('../renderers/MediaPreview', () => ({
  HtmlPreview: ({ content }: { content: string }) => <div data-testid="html-preview">{content}</div>,
  SvgPreview: ({ content }: { content: string }) => <div data-testid="svg-preview">{content}</div>,
}));
vi.mock('../renderers/MermaidPreview', () => ({
  default: ({ content }: { content: string }) => <div data-testid="mermaid-preview">{content}</div>,
}));
vi.mock('../card/ArtifactAppButton', () => ({ ArtifactAppButton: () => null }));
vi.mock('../card/ArtifactSharePopover', () => ({ ArtifactSharePopover: () => null }));
vi.mock('../card/ArtifactLocalFileActions', () => ({ ArtifactLocalFileActions: () => null }));
vi.mock('../card/ArtifactPublicationBanners', () => ({
  ArtifactPublicationBanners: (props: {
    publications: unknown[];
    latestVersionId?: string | null;
    onRedeploy: (targetId: string) => void;
  }) => (
    <div data-testid="banners" data-count={props.publications.length} data-latest={props.latestVersionId ?? ''}>
      <button onClick={() => props.onRedeploy('t1')}>redeploy</button>
    </div>
  ),
}));
vi.mock('../PublishModal', () => ({
  PublishModal: (props: {
    open: boolean;
    initialTargetId?: string;
    onClose: () => void;
    onPublished: (update: { publications: unknown[]; latest_version_id: string }) => void;
  }) => (
    <div data-testid="publish-modal" data-open={String(props.open)} data-target={props.initialTargetId ?? ''}>
      <button onClick={props.onClose}>close-modal</button>
      <button onClick={() => props.onPublished({ publications: [PUBLICATION], latest_version_id: 'v9' })}>
        publish-done
      </button>
    </div>
  ),
}));
vi.mock('../WeChatDraftPanel', () => ({
  WeChatDraftPanel: (props: {
    htmlPath: string;
    onComplete: () => void;
    fetchInlineContent: () => Promise<unknown>;
  }) => (
    <div data-testid="wechat-panel" data-path={props.htmlPath}>
      <button onClick={props.onComplete}>finish-draft</button>
      <button onClick={() => void props.fetchInlineContent()}>load-page</button>
    </div>
  ),
}));
vi.mock('../OrganizePlanPanel', () => ({
  OrganizePlanPanel: (props: { workspace: string; planContent: string }) => (
    <div data-testid="organize-panel" data-workspace={props.workspace}>
      {props.planContent}
    </div>
  ),
}));

export const PUBLICATION = {
  id: 'p1',
  hosting_target_id: 't1',
  publication_url: 'https://live.example.com/a',
  publication_status: 'READY',
  publication_project_ref: null,
  publication_version_id: 'v1',
  updated_at: null,
};
export const SECOND_PUBLICATION = { ...PUBLICATION, id: 'p2', hosting_target_id: 't2' };

export const artifactOf = (overrides: Record<string, unknown> = {}) =>
  ({
    id: 'art-1',
    type: 'html',
    filename: 'page.html',
    preview_url: '/p/page.html',
    download_url: '/d/page.html',
    content: '',
    size: 10,
    ...overrides,
  }) as never;

export const PAGE_URL = 'storage:/p/page.html';
export const PREFLIGHT_URL = 'api:/files/artifacts/art-1/publish/preflight';
export const HYDRATE_URL = 'api:/files/artifacts/art-1';

export const fetchMock = vi.fn(async (url: string) => {
  const route = hoisted.routes.get(url) ?? { ok: false };
  if (route.throws) {
    throw new Error('network down');
  }
  return {
    ok: route.ok,
    json: async () => route.json,
    text: async () => route.text ?? '',
    blob: async () => new Blob([route.text ?? '']),
  };
});
export const storageFetches = () =>
  fetchMock.mock.calls.map(([url]) => url).filter((url) => url.startsWith('storage:'));
export const deployable = (value: boolean) =>
  hoisted.routes.set(PREFLIGHT_URL, {
    ok: true,
    json: { deployable: value, reason: 'r', message: 'm', hint: value ? null : 'h' },
  });

/** Resets the fake backend, caches and mocks before every test of the calling file. */
export function installArtifactCardHarness(): void {
  beforeEach(() => {
    vi.clearAllMocks();
    hoisted.routes.clear();
    hoisted.cache.clear();
    hoisted.chat.files = [];
    fetchMock.mockClear();
    vi.stubGlobal('fetch', fetchMock);
  });
}
