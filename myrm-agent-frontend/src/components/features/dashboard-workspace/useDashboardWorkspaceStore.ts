/**
 * [INPUT] PinWidgetPayload, colSpan, reorder indices
 * [OUTPUT] useDashboardWorkspaceStore (Zustand persistent store for dashboard widgets)
 * [POS] Features -> Dashboard Workspace -> Store
 */

import { create } from 'zustand';
import { persist, createJSONStorage } from 'zustand/middleware';
import type {
  DashboardWorkspaceState,
  PinnedDashboardWidget,
  PinWidgetPayload,
  WidgetColSpan,
} from './dashboardWorkspaceTypes';

export const useDashboardWorkspaceStore = create<DashboardWorkspaceState>()(
  persist(
    (set, get) => ({
      widgets: [],
      isDrawerOpen: false,
      searchQuery: '',
      activeTagFilter: null,

      pinWidget: (payload: PinWidgetPayload) => {
        const existing = get().widgets.find((w) => w.id === payload.id);
        if (existing) {
          return;
        }

        const now = Date.now();
        const newWidget: PinnedDashboardWidget = {
          id: payload.id,
          title: payload.title || 'Untitled Widget',
          language: payload.language || 'html',
          content: payload.content,
          sourceChatId: payload.sourceChatId,
          pinnedAt: now,
          colSpan: payload.colSpan ?? '2',
          tags: payload.tags ?? [payload.language || 'html'],
        };

        set((state) => ({
          widgets: [newWidget, ...state.widgets],
        }));
      },

      unpinWidget: (id: string) => {
        set((state) => ({
          widgets: state.widgets.filter((w) => w.id !== id),
        }));
      },

      togglePinWidget: (payload: PinWidgetPayload) => {
        const isPinned = get().widgets.some((w) => w.id === payload.id);
        if (isPinned) {
          get().unpinWidget(payload.id);
        } else {
          get().pinWidget(payload);
        }
      },

      isWidgetPinned: (id: string) => {
        return get().widgets.some((w) => w.id === id);
      },

      openDrawer: () => {
        set({ isDrawerOpen: true });
      },

      closeDrawer: () => {
        set({ isDrawerOpen: false });
      },

      toggleDrawer: () => {
        set((state) => ({ isDrawerOpen: !state.isDrawerOpen }));
      },

      updateWidgetColSpan: (id: string, colSpan: WidgetColSpan) => {
        set((state) => ({
          widgets: state.widgets.map((w) => {
            if (w.id === id) {
              return { ...w, colSpan };
            }
            return w;
          }),
        }));
      },

      updateWidgetTitle: (id: string, title: string) => {
        const trimmed = title.trim();
        if (!trimmed) {
          return;
        }
        set((state) => ({
          widgets: state.widgets.map((w) => {
            if (w.id === id) {
              return { ...w, title: trimmed };
            }
            return w;
          }),
        }));
      },

      setSearchQuery: (query: string) => {
        set({ searchQuery: query });
      },

      setActiveTagFilter: (tag: string | null) => {
        set({ activeTagFilter: tag });
      },

      reorderWidgets: (startIndex: number, endIndex: number) => {
        set((state) => {
          if (
            startIndex < 0 ||
            startIndex >= state.widgets.length ||
            endIndex < 0 ||
            endIndex >= state.widgets.length ||
            startIndex === endIndex
          ) {
            return state;
          }

          const cloned = [...state.widgets];
          const [moved] = cloned.splice(startIndex, 1);
          if (moved) {
            cloned.splice(endIndex, 0, moved);
          }
          return { widgets: cloned };
        });
      },

      clearAllWidgets: () => {
        set({ widgets: [] });
      },
    }),
    {
      name: 'myrm_dashboard_workspace_storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({
        widgets: state.widgets,
      }),
    },
  ),
);
