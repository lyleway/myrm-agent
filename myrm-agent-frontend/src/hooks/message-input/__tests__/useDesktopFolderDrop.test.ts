import { describe, it, expect, vi, beforeEach } from 'vitest';
import { renderHook, act, waitFor } from '@testing-library/react';
import { toast as sonnerToast } from 'sonner';
import { normalizeDesktopPath, useDesktopFolderDrop } from '../useDesktopFolderDrop';
import * as tauriLib from '@/lib/tauri';
import * as chatService from '@/services/chat';
import * as sessionAccessRefresh from '@/lib/sessionAccessRefresh';
import useChatStore from '@/store/useChatStore';

const mockToast = vi.hoisted(() => ({
  warning: vi.fn(),
  error: vi.fn(),
  success: vi.fn(),
  info: vi.fn(),
}));
vi.mock('@/lib/utils/toast', () => ({ toast: mockToast }));
vi.mock('sonner', () => ({
  toast: {
    error: vi.fn(),
    success: vi.fn(),
  },
}));

vi.mock('@/lib/tauri', () => ({
  isTauriEnvironment: vi.fn(),
  listenTauriEvent: vi.fn(),
}));

vi.mock('@/services/chat', () => ({
  grantSessionAccessRoot: vi.fn(),
}));

vi.mock('@/lib/sessionAccessRefresh', () => ({
  refreshSessionAccessRoots: vi.fn(),
}));

describe('normalizeDesktopPath', () => {
  it('converts Windows backslashes to forward slashes', () => {
    expect(normalizeDesktopPath('C:\\Users\\John\\Documents')).toBe('C:/Users/John/Documents');
  });

  it('removes redundant trailing slashes except for root', () => {
    expect(normalizeDesktopPath('/Users/alice/project/')).toBe('/Users/alice/project');
    expect(normalizeDesktopPath('/')).toBe('/');
    expect(normalizeDesktopPath('C:/')).toBe('C:/');
  });

  it('handles multiple consecutive slashes', () => {
    expect(normalizeDesktopPath('/Users//bob///work/')).toBe('/Users/bob/work');
  });

  it('returns empty string for blank input', () => {
    expect(normalizeDesktopPath('   ')).toBe('');
  });

  it('keeps the whitespace of a folder name, since it is part of the name', () => {
    expect(normalizeDesktopPath('/Users/me/shared ')).toBe('/Users/me/shared ');
    expect(normalizeDesktopPath('/Users/me/shared /')).toBe('/Users/me/shared ');
  });
});

describe('useDesktopFolderDrop', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    useChatStore.setState({
      chatId: 'test-chat-123',
      sessionAccessRoots: [],
    });
  });

  it('does not register Tauri listeners if not in Tauri environment', () => {
    (tauriLib.isTauriEnvironment as unknown as { mockReturnValue: (v: boolean) => void }).mockReturnValue(false);

    renderHook(() => useDesktopFolderDrop());

    expect(tauriLib.listenTauriEvent).not.toHaveBeenCalled();
  });

  it('registers Tauri drag-drop listeners when in Tauri environment', async () => {
    (tauriLib.isTauriEnvironment as unknown as { mockReturnValue: (v: boolean) => void }).mockReturnValue(true);
    const unlistenMock = vi.fn();
    (tauriLib.listenTauriEvent as unknown as { mockResolvedValue: (v: unknown) => void }).mockResolvedValue(
      unlistenMock,
    );

    const { unmount } = renderHook(() => useDesktopFolderDrop());

    await waitFor(() => {
      expect(tauriLib.listenTauriEvent).toHaveBeenCalledWith('tauri://drag-drop', expect.any(Function));
      expect(tauriLib.listenTauriEvent).toHaveBeenCalledWith('tauri://drag-enter', expect.any(Function));
      expect(tauriLib.listenTauriEvent).toHaveBeenCalledWith('tauri://drag-leave', expect.any(Function));
    });

    unmount();
    expect(unlistenMock).toHaveBeenCalled();
  });

  it('grants session access root and refreshes store on dropped paths', async () => {
    (tauriLib.isTauriEnvironment as unknown as { mockReturnValue: (v: boolean) => void }).mockReturnValue(true);
    (chatService.grantSessionAccessRoot as unknown as { mockResolvedValue: (v: unknown) => void }).mockResolvedValue({
      session_access_roots: [{ path: '/Users/test/workspace', writable: true, source: 'desktop_drag_drop' }],
    });

    const onGranted = vi.fn();
    const { result } = renderHook(() => useDesktopFolderDrop({ onFolderGranted: onGranted }));

    await act(async () => {
      await result.current.handleDroppedPaths(['/Users/test/workspace/']);
    });

    expect(chatService.grantSessionAccessRoot).toHaveBeenCalledWith('test-chat-123', '/Users/test/workspace', true);
    expect(sessionAccessRefresh.refreshSessionAccessRoots).toHaveBeenCalledWith('test-chat-123', {
      optimistic: [{ path: '/Users/test/workspace', writable: true, source: 'desktop_drag_drop' }],
    });
    expect(onGranted).toHaveBeenCalledWith('/Users/test/workspace');
  });

  it('grants a dropped folder whose name ends in a space as itself, not as the folder above it', async () => {
    (tauriLib.isTauriEnvironment as unknown as { mockReturnValue: (v: boolean) => void }).mockReturnValue(true);
    (chatService.grantSessionAccessRoot as unknown as { mockResolvedValue: (v: unknown) => void }).mockResolvedValue({
      session_access_roots: [],
    });

    const onGranted = vi.fn();
    const { result } = renderHook(() => useDesktopFolderDrop({ onFolderGranted: onGranted }));

    await act(async () => {
      await result.current.handleDroppedPaths(['/Users/test/shared ']);
    });

    expect(chatService.grantSessionAccessRoot).toHaveBeenCalledWith('test-chat-123', '/Users/test/shared ', true);
    expect(sessionAccessRefresh.refreshSessionAccessRoots).toHaveBeenCalledWith('test-chat-123', {
      optimistic: [{ path: '/Users/test/shared ', writable: true, source: 'desktop_drag_drop' }],
    });
    expect(onGranted).toHaveBeenCalledWith('/Users/test/shared ');
  });

  it('reports a folder the server refuses instead of showing it as granted', async () => {
    (chatService.grantSessionAccessRoot as unknown as { mockRejectedValue: (v: unknown) => void }).mockRejectedValue(
      new Error('Folder access cannot be granted for this chat'),
    );

    const onGranted = vi.fn();
    const { result } = renderHook(() => useDesktopFolderDrop({ onFolderGranted: onGranted }));

    await act(async () => {
      await result.current.handleDroppedPaths(['/Users/test/workspace']);
    });

    expect(sonnerToast.error).toHaveBeenCalledTimes(1);
    expect(sessionAccessRefresh.refreshSessionAccessRoots).not.toHaveBeenCalled();
    expect(onGranted).not.toHaveBeenCalled();
  });

  it.each([
    ['an empty drop', {}, []],
    ['a drop of blank paths only', {}, ['   ', '']],
    ['a drop while disabled', { disabled: true }, ['/Users/test/workspace']],
  ])('grants nothing for %s', async (_name, options, paths) => {
    const { result } = renderHook(() => useDesktopFolderDrop(options));

    await act(async () => {
      await result.current.handleDroppedPaths(paths);
    });

    expect(chatService.grantSessionAccessRoot).not.toHaveBeenCalled();
    expect(sessionAccessRefresh.refreshSessionAccessRoots).not.toHaveBeenCalled();
  });

  describe('native drag events', () => {
    const listeners = new Map<string, (payload: unknown) => void>();

    beforeEach(() => {
      listeners.clear();
      vi.mocked(tauriLib.isTauriEnvironment).mockReturnValue(true);
      vi.mocked(tauriLib.listenTauriEvent).mockImplementation(async (event, handler) => {
        listeners.set(event, handler);
        return () => undefined;
      });
    });

    it('shows the drop overlay from drag-enter until drag-leave or drop', async () => {
      const { result } = renderHook(() => useDesktopFolderDrop());
      await waitFor(() => expect(listeners.size).toBe(3));

      act(() => listeners.get('tauri://drag-enter')?.({}));
      expect(result.current.isDraggingOver).toBe(true);
      act(() => listeners.get('tauri://drag-leave')?.({}));
      expect(result.current.isDraggingOver).toBe(false);

      act(() => listeners.get('tauri://drag-enter')?.({}));
      act(() => listeners.get('tauri://drag-drop')?.({ payload: { paths: [] } }));
      expect(result.current.isDraggingOver).toBe(false);
      expect(chatService.grantSessionAccessRoot).not.toHaveBeenCalled();
    });

    it('grants the folders of a native drop, whether the paths sit in the payload or in the event itself', async () => {
      vi.mocked(chatService.grantSessionAccessRoot).mockResolvedValue({ session_access_roots: [] });
      renderHook(() => useDesktopFolderDrop());
      await waitFor(() => expect(listeners.size).toBe(3));

      act(() => listeners.get('tauri://drag-drop')?.({ payload: { paths: ['/Users/test/a'] } }));
      act(() => listeners.get('tauri://drag-drop')?.({ paths: ['/Users/test/b'] }));

      await waitFor(() => expect(chatService.grantSessionAccessRoot).toHaveBeenCalledTimes(2));
      expect(chatService.grantSessionAccessRoot).toHaveBeenCalledWith('test-chat-123', '/Users/test/a', true);
      expect(chatService.grantSessionAccessRoot).toHaveBeenCalledWith('test-chat-123', '/Users/test/b', true);
    });

    it('stays usable when the listeners cannot be registered', async () => {
      const warn = vi.spyOn(console, 'warn').mockImplementation(() => undefined);
      vi.mocked(tauriLib.listenTauriEvent).mockRejectedValue(new Error('no tauri event api'));

      const { result } = renderHook(() => useDesktopFolderDrop());

      await waitFor(() =>
        expect(warn).toHaveBeenCalledWith('Failed to register Tauri drag-drop listeners:', expect.any(Error)),
      );
      expect(result.current.isDraggingOver).toBe(false);
      warn.mockRestore();
    });
  });
});
