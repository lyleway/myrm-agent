/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../ArtifactCard::ArtifactCard
 *
 * [OUTPUT]
 * - Tests: the card's inline preview, the organize-plan and WeChat draft panels, the publish flow and the card actions.
 *
 * [POS]
 * What a user sees and which requests a chat artifact card makes. The card's collaborators (preview renderers, publish
 * modal, panels) are replaced by thin stand-ins so each assertion is about the card's own wiring.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

interface FakeResponse {
  ok: boolean;
  json?: unknown;
  text?: string;
}

const env = vi.hoisted(() => {
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

const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT, useLocale: () => 'en' }));
vi.mock('@/lib/api', () => ({
  apiRequest: vi.fn().mockResolvedValue({}),
  getApiUrl: (path: string) => `api:${path}`,
  getStorageUrl: (url: string) => `storage:${url}`,
}));
vi.mock('@/store/useChatStore', () => {
  const hook = (selector: (state: typeof env.chat) => unknown) => selector(env.chat);
  return { default: Object.assign(hook, { getState: () => env.chat }) };
});
vi.mock('@/store/useArtifactPortalStore', () => ({ default: () => env.cacheApi }));
vi.mock('sonner', () => ({ toast: env.toast }));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: () => false }));
vi.mock('@/lib/utils/clipboardUtils', () => ({ writeToClipboard: env.writeToClipboard }));
vi.mock('@/lib/progression/tryMarkMilestone', () => ({ tryMarkMilestone: env.tryMarkMilestone }));
vi.mock('@/services/wikiService', () => ({ wikiService: { ingestArtifact: env.ingestArtifact } }));
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
  WeChatDraftPanel: (props: { htmlPath: string; onComplete: () => void; fetchInlineContent: () => Promise<unknown> }) => (
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

import ArtifactCard from '../ArtifactCard';
import { LARGE_FILE_THRESHOLD } from '@/lib/constants/artifact';

const PUBLICATION = {
  id: 'p1',
  hosting_target_id: 't1',
  publication_url: 'https://live.example.com/a',
  publication_status: 'READY',
  publication_project_ref: null,
  publication_version_id: 'v1',
  updated_at: null,
};
const SECOND_PUBLICATION = { ...PUBLICATION, id: 'p2', hosting_target_id: 't2' };

const artifactOf = (overrides: Record<string, unknown> = {}) =>
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

const PAGE_URL = 'storage:/p/page.html';
const PREFLIGHT_URL = 'api:/files/artifacts/art-1/publish/preflight';
const HYDRATE_URL = 'api:/files/artifacts/art-1';

const fetchMock = vi.fn(async (url: string) => {
  const route = env.routes.get(url) ?? { ok: false };
  return {
    ok: route.ok,
    json: async () => route.json,
    text: async () => route.text ?? '',
    blob: async () => new Blob([route.text ?? '']),
  };
});
const storageFetches = () => fetchMock.mock.calls.map(([url]) => url).filter((url) => url.startsWith('storage:'));
const deployable = (value: boolean) =>
  env.routes.set(PREFLIGHT_URL, {
    ok: true,
    json: { deployable: value, reason: 'r', message: 'm', hint: value ? null : 'h' },
  });

beforeEach(() => {
  vi.clearAllMocks();
  env.routes.clear();
  env.cache.clear();
  env.chat.files = [];
  fetchMock.mockClear();
  vi.stubGlobal('fetch', fetchMock);
});

describe('ArtifactCard inline preview', () => {
  it('opens an HTML artifact inline and loads its page once', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '<p>hi</p>' });

    render(<ArtifactCard artifact={artifactOf()} />);

    expect(await screen.findByTestId('html-preview')).toHaveTextContent('<p>hi</p>');
    expect(storageFetches()).toEqual([PAGE_URL]);
  });

  it.each([
    ['svg', 'a.svg', 'svg-preview'],
    ['mermaid', 'a.mmd', 'mermaid-preview'],
  ])('renders a %s artifact with its own renderer', async (type, filename, testId) => {
    env.routes.set(PAGE_URL, { ok: true, text: 'BODY' });

    render(<ArtifactCard artifact={artifactOf({ type, filename })} />);

    expect(await screen.findByTestId(testId)).toHaveTextContent('BODY');
  });

  it('folds and unfolds without loading the page again', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '<p>hi</p>' });
    render(<ArtifactCard artifact={artifactOf()} />);
    await screen.findByTestId('html-preview');

    fireEvent.click(screen.getByTitle('inlinePreview.collapse'));
    expect(screen.queryByTestId('html-preview')).not.toBeInTheDocument();
    fireEvent.click(screen.getByTitle('inlinePreview.expand'));

    expect(await screen.findByTestId('html-preview')).toHaveTextContent('<p>hi</p>');
    expect(storageFetches()).toEqual([PAGE_URL]);
  });

  it('says the page could not be loaded, and tries again when it is unfolded again', async () => {
    render(<ArtifactCard artifact={artifactOf()} />);
    expect(await screen.findByText('inlinePreview.loadError')).toBeInTheDocument();
    env.routes.set(PAGE_URL, { ok: true, text: '<p>back</p>' });

    fireEvent.click(screen.getByTitle('inlinePreview.collapse'));
    fireEvent.click(screen.getByTitle('inlinePreview.expand'));

    expect(await screen.findByTestId('html-preview')).toHaveTextContent('<p>back</p>');
    expect(storageFetches()).toHaveLength(2);
  });

  it('does not load a large page inline and offers the full-screen view instead', async () => {
    const onPreview = vi.fn();
    const artifact = artifactOf({ size: LARGE_FILE_THRESHOLD + 1 });
    render(<ArtifactCard artifact={artifact} onPreview={onPreview} />);

    expect(screen.getByText('inlinePreview.largeFileHint')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: /inlinePreview.viewFullscreen/ }));

    expect(onPreview).toHaveBeenCalledWith(artifact);
    expect(storageFetches()).toEqual([]);
  });

  it('warms the content cache when the pointer rests on the card, not when it only passes', async () => {
    const { container } = render(<ArtifactCard artifact={artifactOf({ type: 'code', filename: 'a.py' })} />);
    const card = container.firstElementChild as HTMLElement;

    fireEvent.mouseEnter(card);
    fireEvent.mouseLeave(card);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 350));
    });
    expect(storageFetches()).toEqual([]);

    env.routes.set('storage:/p/page.html', { ok: true, text: 'print(1)' });
    fireEvent.mouseEnter(card);
    await waitFor(() => expect(storageFetches()).toEqual([PAGE_URL]));
    expect(env.cache.get('art-1')).toBe('print(1)');
  });
});

describe('ArtifactCard organize plan', () => {
  const planArtifact = artifactOf({ type: 'code', filename: 'x.organize-plan.json' });

  it('opens the plan loaded from its file', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '{"moves":[]}' });
    render(<ArtifactCard artifact={planArtifact} />);

    fireEvent.click(screen.getByTitle('organizePlan.openPanel'));

    const panel = await screen.findByTestId('organize-panel');
    expect(panel).toHaveTextContent('{"moves":[]}');
    expect(panel).toHaveAttribute('data-workspace', '/ws');
  });

  it('says the plan is invalid when its file cannot be read, and reads it again on the next opening', async () => {
    render(<ArtifactCard artifact={planArtifact} />);
    const toggle = screen.getByTitle('organizePlan.openPanel');

    fireEvent.click(toggle);
    expect(await screen.findByText('organizePlan.invalidPlan')).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByText('organizePlan.invalidPlan')).not.toBeInTheDocument();
    env.routes.set(PAGE_URL, { ok: true, text: '{"moves":[1]}' });
    fireEvent.click(toggle);

    expect(await screen.findByTestId('organize-panel')).toHaveTextContent('{"moves":[1]}');
    expect(storageFetches()).toHaveLength(2);
  });

  it('keeps a loaded plan while the panel is closed and reopened', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '{"moves":[]}' });
    render(<ArtifactCard artifact={planArtifact} />);
    const toggle = screen.getByTitle('organizePlan.openPanel');
    fireEvent.click(toggle);
    await screen.findByTestId('organize-panel');

    fireEvent.click(toggle);
    fireEvent.click(toggle);

    expect(await screen.findByTestId('organize-panel')).toBeInTheDocument();
    expect(storageFetches()).toHaveLength(1);
  });
});

describe('ArtifactCard WeChat draft', () => {
  it('opens and closes the draft panel for an HTML file on disk', () => {
    render(<ArtifactCard artifact={artifactOf({ file_path: '/ws/page.html' })} />);

    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));
    expect(screen.getByTestId('wechat-panel')).toHaveAttribute('data-path', '/ws/page.html');
    fireEvent.click(screen.getByText('finish-draft'));
    expect(screen.queryByTestId('wechat-panel')).not.toBeInTheDocument();

    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));
    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));
    expect(screen.queryByTestId('wechat-panel')).not.toBeInTheDocument();
  });

  it('gives the panel the card page loader', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: '<p>hi</p>' });
    render(<ArtifactCard artifact={artifactOf({ file_path: '/ws/page.html' })} />);
    await waitFor(() => expect(env.cache.get('art-1')).toBe('<p>hi</p>'));
    fireEvent.click(screen.getByTestId('wechat-draft-open-panel'));

    fireEvent.click(screen.getByText('load-page'));

    expect(storageFetches()).toEqual([PAGE_URL]);
  });

  it('is not offered without a file on disk', () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    expect(screen.queryByTestId('wechat-draft-open-panel')).not.toBeInTheDocument();
  });
});

describe('ArtifactCard publishing', () => {
  it('refuses to open the publish dialog and says why when the artifact cannot be published', async () => {
    deployable(false);
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(await screen.findByTitle('h'));

    expect(env.toast.error).toHaveBeenCalledWith('m', { description: 'h' });
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'false');
  });

  it('opens the publish dialog when the artifact can be published', async () => {
    deployable(true);
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(await screen.findByTitle('publish.openModal'));

    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'true');
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-target', '');
  });

  it('opens the dialog on a given target to republish, and forgets the target when closed', () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByText('redeploy'));
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'true');
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-target', 't1');
    fireEvent.click(screen.getByText('close-modal'));

    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-open', 'false');
    expect(screen.getByTestId('publish-modal')).toHaveAttribute('data-target', '');
  });

  it('shows the live link of a published artifact and opens it', async () => {
    deployable(true);
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    render(<ArtifactCard artifact={artifactOf({ publications: [PUBLICATION] })} />);

    fireEvent.click(await screen.findByTitle('deploy.deployedLabel'));

    expect(open).toHaveBeenCalledWith('https://live.example.com/a', '_blank');
  });

  it('takes the publications and latest version from the server', async () => {
    env.routes.set(HYDRATE_URL, { ok: true, json: { latest_version_id: 'v5', publications: [PUBLICATION] } });

    render(<ArtifactCard artifact={artifactOf()} />);

    await waitFor(() => expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '1'));
    expect(screen.getByTestId('banners')).toHaveAttribute('data-latest', 'v5');
    expect(env.chat.updateMessages).toHaveBeenCalledTimes(1);
  });

  it('does not rewrite the chat when the server reports what the card already shows', async () => {
    env.routes.set(HYDRATE_URL, { ok: true, json: { publications: [PUBLICATION] } });

    render(<ArtifactCard artifact={artifactOf({ publications: [PUBLICATION] })} />);

    await waitFor(() => expect(fetchMock).toHaveBeenCalledWith(HYDRATE_URL));
    await act(async () => {});
    expect(env.chat.updateMessages).not.toHaveBeenCalled();
  });

  it('records a publication made in the dialog', async () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByText('publish-done'));

    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '1');
    expect(screen.getByTestId('banners')).toHaveAttribute('data-latest', 'v9');
    expect(env.chat.updateMessages).toHaveBeenCalledTimes(1);
    await waitFor(() => expect(env.tryMarkMilestone).toHaveBeenCalledWith('first_multistep_delivery'));
  });

  it('follows a changed artifact, and keeps the publications it knew when the new one has none', () => {
    const { rerender } = render(<ArtifactCard artifact={artifactOf({ publications: [PUBLICATION] })} />);
    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '1');

    rerender(
      <ArtifactCard
        artifact={artifactOf({ filename: 'new.html', publications: [PUBLICATION, SECOND_PUBLICATION] })}
      />,
    );
    expect(screen.getByRole('heading', { name: 'new.html' })).toBeInTheDocument();
    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '2');

    rerender(<ArtifactCard artifact={artifactOf({ filename: 'newer.html', publications: [] })} />);
    expect(screen.getByRole('heading', { name: 'newer.html' })).toBeInTheDocument();
    expect(screen.getByTestId('banners')).toHaveAttribute('data-count', '2');
  });

  it('only checks publishing for artifacts that can be published', () => {
    render(<ArtifactCard artifact={artifactOf({ type: 'binary', filename: 'a.bin' })} />);

    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain(PREFLIGHT_URL);
    expect(fetchMock.mock.calls.map(([url]) => url)).not.toContain(HYDRATE_URL);
  });
});

describe('ArtifactCard actions', () => {
  it('copies the content and shows the confirmation', async () => {
    env.routes.set(PAGE_URL, { ok: true, text: 'print(1)' });
    render(<ArtifactCard artifact={artifactOf({ type: 'code', filename: 'a.py' })} />);

    fireEvent.click(screen.getByTitle('copyCode'));

    await waitFor(() => expect(env.writeToClipboard).toHaveBeenCalledWith('print(1)'));
    expect(await screen.findByTitle('copied')).toBeInTheDocument();
  });

  it('attaches the artifact to the chat once', () => {
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('insertToChat.title'));
    expect(env.chat.files).toEqual([
      expect.objectContaining({ fileName: 'page.html', fileUrl: PAGE_URL, fileType: 'uploaded' }),
    ]);
    expect(env.toast.success).toHaveBeenCalledWith('insertToChat.success');

    fireEvent.click(screen.getByTitle('insertToChat.title'));
    expect(env.chat.files).toHaveLength(1);
    expect(env.toast.info).toHaveBeenCalledWith('insertToChat.alreadyAttached');
  });

  it('adds the artifact to the knowledge base for the chat agent, and reports the outcome', async () => {
    env.ingestArtifact.mockResolvedValueOnce({ success: true, message: 'ok' });
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('ingestToWiki.title'));

    await waitFor(() => expect(env.toast.success).toHaveBeenCalledWith('ingestToWiki.success', { description: 'ok' }));
    expect(env.ingestArtifact).toHaveBeenCalledWith('art-1', 'agent-7');

    env.ingestArtifact.mockResolvedValueOnce({ success: false, message: 'bad' });
    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    await waitFor(() =>
      expect(env.toast.error).toHaveBeenCalledWith('ingestToWiki.failed', { description: 'bad' }),
    );

    env.ingestArtifact.mockRejectedValueOnce(new Error('offline'));
    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    await waitFor(() => expect(env.toast.error).toHaveBeenLastCalledWith('ingestToWiki.failed'));
  });

  it('ignores a second knowledge-base click while the first is still running', async () => {
    let finish: (value: { success: boolean; message: string }) => void = () => undefined;
    env.ingestArtifact.mockReturnValueOnce(new Promise((resolve) => (finish = resolve)));
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    expect(screen.getByTitle('ingestToWiki.title')).toBeDisabled();
    fireEvent.click(screen.getByTitle('ingestToWiki.title'));
    await act(async () => finish({ success: true, message: 'ok' }));

    expect(env.ingestArtifact).toHaveBeenCalledTimes(1);
    expect(screen.getByTitle('ingestToWiki.title')).toBeEnabled();
  });

  it('hands the artifact to the download and preview callbacks', () => {
    const onDownload = vi.fn();
    const onPreview = vi.fn();
    const artifact = artifactOf();
    render(<ArtifactCard artifact={artifact} onDownload={onDownload} onPreview={onPreview} />);

    fireEvent.click(screen.getByTitle('download'));
    fireEvent.click(screen.getByTitle('preview'));
    fireEvent.click(screen.getByRole('heading', { name: 'page.html' }));

    expect(onDownload).toHaveBeenCalledWith(artifact);
    expect(onPreview).toHaveBeenCalledTimes(2);
    expect(onPreview).toHaveBeenCalledWith(artifact);
  });

  it('opens the artifact in a new tab', () => {
    const open = vi.spyOn(window, 'open').mockReturnValue(null);
    render(<ArtifactCard artifact={artifactOf()} />);

    fireEvent.click(screen.getByTitle('openInNewTab'));

    expect(open).toHaveBeenCalledWith(PAGE_URL, '_blank', 'noopener,noreferrer');
  });
});
