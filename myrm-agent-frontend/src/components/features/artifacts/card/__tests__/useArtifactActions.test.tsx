/** @vitest-environment jsdom */

import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => {
  const chat = {
    agentConfig: { agentId: 'agent-7' } as { agentId: string } | undefined,
    files: [] as Array<Record<string, unknown>>,
    setFiles: (files: Array<Record<string, unknown>>) => {
      chat.files = files;
    },
  };
  return {
    chat,
    isTauriRuntime: vi.fn(() => false),
    writeToClipboard: vi.fn(),
    ingestArtifact: vi.fn(),
    toast: { success: vi.fn(), error: vi.fn(), info: vi.fn() },
  };
});

const stableT = vi.hoisted(() => (key: string) => key);
vi.mock('next-intl', () => ({ useTranslations: () => stableT }));
vi.mock('sonner', () => ({ toast: mocks.toast }));
vi.mock('@/lib/api', () => ({ getStorageUrl: (url: string) => `storage:${url}` }));
vi.mock('@/lib/deploy-mode', () => ({ isTauriRuntime: mocks.isTauriRuntime }));
vi.mock('@/lib/utils/clipboardUtils', () => ({ writeToClipboard: mocks.writeToClipboard }));
vi.mock('@/services/wikiService', () => ({ wikiService: { ingestArtifact: mocks.ingestArtifact } }));
vi.mock('@/store/useChatStore', () => ({ default: { getState: () => mocks.chat } }));
vi.mock('../../artifactUtils', () => ({ getDownloadFilename: (name: string) => `dl-${name}` }));

import { useArtifactActions } from '../useArtifactActions';

const artifact = (overrides: Record<string, unknown> = {}) =>
  ({
    id: 'art-1',
    filename: 'page.html',
    preview_url: '/p/page.html',
    download_url: '/d/page.html',
    ...overrides,
  }) as never;

function setup(
  options: { fetchContent?: () => Promise<string | null>; onDownload?: (a: never) => void; art?: never } = {},
) {
  const art = options.art ?? artifact();
  return renderHook(() =>
    useArtifactActions(art, art, options.fetchContent ?? (async () => 'content'), options.onDownload as never),
  );
}

describe('useArtifactActions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.chat.files = [];
    mocks.chat.agentConfig = { agentId: 'agent-7' };
    mocks.isTauriRuntime.mockReturnValue(false);
    mocks.writeToClipboard.mockResolvedValue(undefined);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  describe('copy', () => {
    it('copies the content and shows the confirmation for two seconds', async () => {
      vi.useFakeTimers();
      const { result } = setup();

      await act(async () => result.current.copy());

      expect(mocks.writeToClipboard).toHaveBeenCalledWith('content');
      expect(result.current.copied).toBe(true);
      act(() => {
        vi.advanceTimersByTime(2000);
      });
      expect(result.current.copied).toBe(false);
    });

    it('copies nothing when the content is empty or cannot be loaded', async () => {
      const { result } = setup({ fetchContent: async () => null });

      await act(async () => result.current.copy());

      expect(mocks.writeToClipboard).not.toHaveBeenCalled();
      expect(result.current.copied).toBe(false);
    });

    it('stays quiet when the clipboard refuses', async () => {
      mocks.writeToClipboard.mockRejectedValue(new Error('insecure context'));
      const { result } = setup();

      await act(async () => result.current.copy());

      expect(result.current.copied).toBe(false);
    });
  });

  it('opens the stored file in a new tab that cannot reach back into the app', () => {
    const open = vi.spyOn(window, 'open').mockImplementation(() => null);
    const { result } = setup();

    result.current.openInNewTab();

    expect(open).toHaveBeenCalledWith('storage:/p/page.html', '_blank', 'noopener,noreferrer');
  });

  describe('ingestToWiki', () => {
    it('reports success with the server message, for the agent in use', async () => {
      mocks.ingestArtifact.mockResolvedValue({ success: true, message: 'saved' });
      const { result } = setup();

      await act(async () => result.current.ingestToWiki());

      expect(mocks.ingestArtifact).toHaveBeenCalledWith('art-1', 'agent-7');
      expect(mocks.toast.success).toHaveBeenCalledWith('ingestToWiki.success', { description: 'saved' });
      expect(result.current.ingestLoading).toBe(false);
    });

    it('works without an agent', async () => {
      mocks.chat.agentConfig = undefined;
      mocks.ingestArtifact.mockResolvedValue({ success: true, message: 'saved' });
      const { result } = setup();

      await act(async () => result.current.ingestToWiki());

      expect(mocks.ingestArtifact).toHaveBeenCalledWith('art-1', undefined);
    });

    it('reports a refusal with the server message', async () => {
      mocks.ingestArtifact.mockResolvedValue({ success: false, message: 'too large' });
      const { result } = setup();

      await act(async () => result.current.ingestToWiki());

      expect(mocks.toast.error).toHaveBeenCalledWith('ingestToWiki.failed', { description: 'too large' });
    });

    it('reports a failed request and lets the user try again', async () => {
      mocks.ingestArtifact.mockRejectedValue(new Error('offline'));
      const { result } = setup();

      await act(async () => result.current.ingestToWiki());

      expect(mocks.toast.error).toHaveBeenCalledWith('ingestToWiki.failed');
      expect(result.current.ingestLoading).toBe(false);
    });

    it('ignores a second request while the first one is running', async () => {
      let finish: (value: { success: boolean; message: string }) => void = () => undefined;
      mocks.ingestArtifact.mockReturnValue(new Promise((resolve) => (finish = resolve)));
      const { result } = setup();

      act(() => {
        void result.current.ingestToWiki();
      });
      expect(result.current.ingestLoading).toBe(true);
      await act(async () => result.current.ingestToWiki());
      await act(async () => finish({ success: true, message: 'saved' }));

      expect(mocks.ingestArtifact).toHaveBeenCalledTimes(1);
      expect(result.current.ingestLoading).toBe(false);
    });
  });

  describe('insertToChat', () => {
    it('attaches an uploaded file by its storage address', () => {
      const { result } = setup();

      result.current.insertToChat();

      expect(mocks.chat.files).toEqual([
        {
          fileName: 'page.html',
          fileExtension: 'html',
          localPath: undefined,
          fileUrl: 'storage:/p/page.html',
          fileType: 'uploaded',
        },
      ]);
      expect(mocks.toast.success).toHaveBeenCalledWith('insertToChat.success');
    });

    it('attaches a file that lives on disk by its path on the desktop', () => {
      mocks.isTauriRuntime.mockReturnValue(true);
      const { result } = setup({ art: artifact({ file_path: '/ws/page.html' }) });

      result.current.insertToChat();

      expect(mocks.chat.files).toEqual([
        {
          fileName: 'page.html',
          fileExtension: 'html',
          localPath: '/ws/page.html',
          fileUrl: undefined,
          fileType: 'local_path',
        },
      ]);
    });

    it('uses the storage address for a file with a path when it is not the desktop', () => {
      const { result } = setup({ art: artifact({ file_path: '/ws/page.html' }) });

      result.current.insertToChat();

      expect(mocks.chat.files[0]).toMatchObject({ fileType: 'uploaded', fileUrl: 'storage:/p/page.html' });
    });

    it('does not attach the same file twice', () => {
      const { result } = setup();
      result.current.insertToChat();
      mocks.toast.success.mockClear();

      result.current.insertToChat();

      expect(mocks.chat.files).toHaveLength(1);
      expect(mocks.toast.info).toHaveBeenCalledWith('insertToChat.alreadyAttached');
      expect(mocks.toast.success).not.toHaveBeenCalled();
    });

    it('does not mistake the same path of a differently named file for an attachment', () => {
      mocks.isTauriRuntime.mockReturnValue(true);
      mocks.chat.files = [{ fileName: 'other.html', localPath: '/ws/page.html' }];
      const { result } = setup({ art: artifact({ file_path: '/ws/page.html' }) });

      result.current.insertToChat();

      expect(mocks.chat.files).toHaveLength(2);
    });

    it('gives a name that ends with a dot an empty extension', () => {
      const { result } = setup({ art: artifact({ filename: 'weird.' }) });

      result.current.insertToChat();

      expect(mocks.chat.files[0]).toMatchObject({ fileName: 'weird.', fileExtension: '' });
    });
  });

  describe('download', () => {
    it('hands the artifact to the caller when it wants to download itself', async () => {
      const onDownload = vi.fn();
      const fetchSpy = vi.fn();
      vi.stubGlobal('fetch', fetchSpy);
      const { result } = setup({ onDownload });

      await act(async () => result.current.download());

      expect(onDownload).toHaveBeenCalledWith(expect.objectContaining({ id: 'art-1' }));
      expect(fetchSpy).not.toHaveBeenCalled();
      vi.unstubAllGlobals();
    });

    it('saves the file under its download name through a temporary link that is cleaned up', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => ({ blob: async () => new Blob(['x']) })),
      );
      const createObjectURL = vi.fn(() => 'blob:page');
      const revokeObjectURL = vi.fn();
      Object.assign(window.URL, { createObjectURL, revokeObjectURL });
      const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (
        this: HTMLAnchorElement,
      ) {
        expect(this.download).toBe('dl-page.html');
        expect(this.href).toBe('blob:page');
        expect(document.body.contains(this)).toBe(true);
      });
      const { result } = setup();

      await act(async () => result.current.download());

      expect(fetch).toHaveBeenCalledWith('/d/page.html');
      expect(click).toHaveBeenCalledTimes(1);
      expect(document.querySelector('a[download]')).toBeNull();
      expect(revokeObjectURL).toHaveBeenCalledWith('blob:page');
      vi.unstubAllGlobals();
    });

    it('logs and gives up when the file cannot be fetched', async () => {
      vi.stubGlobal(
        'fetch',
        vi.fn(async () => Promise.reject(new Error('offline'))),
      );
      const error = vi.spyOn(console, 'error').mockImplementation(() => undefined);
      const { result } = setup();

      await act(async () => result.current.download());

      expect(error).toHaveBeenCalledWith('Download failed:', expect.any(Error));
      vi.unstubAllGlobals();
    });
  });
});
