'use client';

import { useState, useEffect, useCallback, useRef } from 'react';
import { usePathname } from 'next/navigation';
import { checkIsMobile } from '@/lib/utils/device';
import { requestManager } from '@/lib/utils/requestManager';
import { isMobileRemotePath } from '@/lib/mobileRemote';
import { useResizableSidebar } from '@/hooks/ui/useResizableSidebar';
import type { NavTab } from './NavBar';

export const NAVBAR_WIDTH = 60;

const ROUTE_STORAGE_KEY = 'myrm_last_tab_routes';

function readSavedRoutes(): Record<string, string> {
  if (typeof window === 'undefined') {
    return {};
  }
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(ROUTE_STORAGE_KEY) || '{}');
    // A stored `null` or array parses fine but is not a route map; reading a tab off it would take the whole shell down.
    if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) {
      return {};
    }
    // A page is only restored while it belongs to the tab it was saved under; otherwise that tab would open a page
    // that belongs to another tab.
    return Object.fromEntries(
      Object.entries(parsed).filter(
        (entry): entry is [string, string] => typeof entry[1] === 'string' && getTabFromPathname(entry[1]) === entry[0],
      ),
    );
  } catch {
    return {};
  }
}

function persistRoute(tab: string, url: string) {
  if (typeof window === 'undefined') {
    return;
  }
  try {
    const saved = readSavedRoutes();
    saved[tab] = url;
    localStorage.setItem(ROUTE_STORAGE_KEY, JSON.stringify(saved));
  } catch {
    /* quota exceeded — non-critical */
  }
}

/** Pages grouped under the Projects tab: the dashboard, its boards and schedules, artifacts and the apps installed from them. */
function isProjectsPath(pathname: string): boolean {
  return (
    pathname === '/projects' ||
    pathname.startsWith('/projects/') ||
    pathname.startsWith('/kanban') ||
    pathname.startsWith('/cron') ||
    pathname.startsWith('/artifacts') ||
    pathname.startsWith('/apps/')
  );
}

/** The tab whose remembered page this path becomes; null for pages no tab restores (settings, research, ...). */
function rememberedTabOf(pathname: string): NavTab | null {
  if (pathname === '/' || pathname.startsWith('/c-')) {
    return 'chat';
  }
  if (pathname === '/work' || pathname.startsWith('/work/')) {
    return 'work';
  }
  return isProjectsPath(pathname) ? 'projects' : null;
}

function getTabFromPathname(pathname: string): NavTab {
  if (pathname === '/work' || pathname.startsWith('/work/')) {
    return 'work';
  }
  if (isProjectsPath(pathname)) {
    return 'projects';
  }
  return 'chat';
}

export function useAppLayoutState() {
  const pathname = usePathname();

  const [isMobile, setIsMobile] = useState(false);
  const [activeTab, setActiveTab] = useState<NavTab>(() => getTabFromPathname(pathname));
  // Navigation moves the active tab; a tab click moves it first, ahead of the navigation it triggers.
  const [tabPathname, setTabPathname] = useState(pathname);
  if (tabPathname !== pathname) {
    setTabPathname(pathname);
    setActiveTab(getTabFromPathname(pathname));
  }
  const [isSidebarCollapsed, setIsSidebarCollapsed] = useState(false);
  const [isMobileSidebarOpen, setIsMobileSidebarOpen] = useState(false);
  const [isNavButtonHidden, setIsNavButtonHidden] = useState(false);
  const [savedRoutes] = useState(readSavedRoutes);
  const [lastUrls, setLastUrls] = useState<Partial<Record<NavTab, string>>>(savedRoutes);
  const lastScrollPositionRef = useRef(0);
  const scrollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSidebarCollapse = useCallback(() => {
    setIsSidebarCollapsed(true);
  }, []);

  const {
    width: sidebarWidth,
    isDragging: isSidebarDragging,
    handleMouseDown: handleSidebarResizeStart,
    handleDoubleClick: handleSidebarDoubleClick,
  } = useResizableSidebar({ onCollapse: handleSidebarCollapse });

  useEffect(() => {
    const tab = rememberedTabOf(pathname);
    if (!tab) {
      return;
    }
    // The first client render must match the server render, so the current page joins the remembered pages after mount.
    // oxlint-disable-next-line react/set-state-in-effect -- same reason: remembered pages come from browser storage
    setLastUrls((prev) => (prev[tab] === pathname ? prev : { ...prev, [tab]: pathname }));
    persistRoute(tab, pathname);
  }, [pathname]);

  useEffect(() => {
    const initialMobile = checkIsMobile();
    if (initialMobile !== isMobile) {
      // The viewport is only measurable in the browser, so the server render starts desktop-first.
      // oxlint-disable-next-line react/set-state-in-effect -- measuring the viewport is the external sync this effect exists for
      setIsMobile(initialMobile);
      if (initialMobile) {
        setIsSidebarCollapsed(true);
      }
    }

    let resizeTimer: ReturnType<typeof setTimeout> | null = null;
    const handleResize = () => {
      if (resizeTimer) {
        clearTimeout(resizeTimer);
      }
      resizeTimer = setTimeout(() => {
        const mobile = checkIsMobile();
        if (mobile !== isMobile) {
          setIsMobile(mobile);
          if (mobile) {
            setIsSidebarCollapsed(true);
          }
        }
      }, 100);
    };

    window.addEventListener('resize', handleResize);
    return () => {
      if (resizeTimer) {
        clearTimeout(resizeTimer);
      }
      window.removeEventListener('resize', handleResize);
    };
  }, [isMobile]);

  useEffect(() => () => requestManager.cancelAllRequests(), []);

  useEffect(() => {
    if (!isMobile) {
      return;
    }

    const handleScroll = () => {
      const currentScrollPosition = window.scrollY;
      const scrollDelta = currentScrollPosition - lastScrollPositionRef.current;

      if (scrollDelta > 10 && currentScrollPosition > 100) {
        setIsNavButtonHidden(true);
      }
      if (scrollDelta < -10) {
        setIsNavButtonHidden(false);
      }
      if (currentScrollPosition < 50) {
        setIsNavButtonHidden(false);
      }

      if (scrollTimerRef.current) {
        clearTimeout(scrollTimerRef.current);
      }
      scrollTimerRef.current = setTimeout(() => setIsNavButtonHidden(false), 1500);
      lastScrollPositionRef.current = currentScrollPosition;
    };

    window.addEventListener('scroll', handleScroll, { passive: true });
    return () => {
      window.removeEventListener('scroll', handleScroll);
      if (scrollTimerRef.current) {
        clearTimeout(scrollTimerRef.current);
      }
    };
  }, [isMobile]);

  const handleToggleSidebar = useCallback(() => {
    setIsSidebarCollapsed((prev) => !prev);
  }, []);

  const handleTabChange = useCallback((tab: NavTab) => {
    setActiveTab(tab);
  }, []);

  const isSettingsPage = pathname.startsWith('/settings');
  const isResearchPage = pathname === '/research';
  const isRemoteSurface = isMobileRemotePath(pathname);
  // The research workbench lays out its own three panes, so it gets the full width instead of sharing it with the chat list.
  const shouldShowContentSidebar = !isSettingsPage && !isResearchPage && activeTab === 'chat';
  const showMobileLayout = isMobile;
  const paddingLeft = showMobileLayout
    ? '0px'
    : !shouldShowContentSidebar
      ? `${NAVBAR_WIDTH}px`
      : isSidebarCollapsed
        ? `${NAVBAR_WIDTH}px`
        : `${NAVBAR_WIDTH + sidebarWidth}px`;

  return {
    pathname,
    activeTab,
    isSidebarCollapsed,
    isMobileSidebarOpen,
    setIsMobileSidebarOpen,
    isNavButtonHidden,
    lastChatUrl: lastUrls.chat ?? null,
    lastWorkUrl: lastUrls.work ?? null,
    lastProjectsUrl: lastUrls.projects ?? null,
    sidebarWidth,
    isSidebarDragging,
    handleSidebarResizeStart,
    handleSidebarDoubleClick,
    handleToggleSidebar,
    handleTabChange,
    isSettingsPage,
    isResearchPage,
    isRemoteSurface,
    shouldShowContentSidebar,
    showMobileLayout,
    paddingLeft,
  };
}
