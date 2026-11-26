/**
 * [INPUT] Pinned widget metadata, layout config, and workspace state actions
 * [OUTPUT] Dashboard workspace type definitions and contracts
 * [POS] Features -> Dashboard Workspace -> Types
 */

export type WidgetColSpan = '1' | '2' | '3';

export interface PinnedDashboardWidget {
  id: string;
  title: string;
  language: string;
  content: string;
  sourceChatId?: string;
  pinnedAt: number;
  colSpan: WidgetColSpan;
  tags: string[];
}

export interface PinWidgetPayload {
  id: string;
  title: string;
  language: string;
  content: string;
  sourceChatId?: string;
  colSpan?: WidgetColSpan;
  tags?: string[];
}

export interface DashboardWorkspaceState {
  widgets: PinnedDashboardWidget[];
  isDrawerOpen: boolean;
  searchQuery: string;
  activeTagFilter: string | null;
  pinWidget: (payload: PinWidgetPayload) => void;
  unpinWidget: (id: string) => void;
  togglePinWidget: (payload: PinWidgetPayload) => void;
  isWidgetPinned: (id: string) => boolean;
  openDrawer: () => void;
  closeDrawer: () => void;
  toggleDrawer: () => void;
  updateWidgetColSpan: (id: string, colSpan: WidgetColSpan) => void;
  updateWidgetTitle: (id: string, title: string) => void;
  setSearchQuery: (query: string) => void;
  setActiveTagFilter: (tag: string | null) => void;
  reorderWidgets: (startIndex: number, endIndex: number) => void;
  clearAllWidgets: () => void;
}
