import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, fireEvent, act } from '@testing-library/react';
import React from 'react';
import type { WebFetchEscalationConfigValue } from '@/services/config';
import WebFetchEscalationCard from '../WebFetchEscalationCard';

const configStore = new Map<string, WebFetchEscalationConfigValue>();
const setConfigMock = vi.fn((key: string, value: WebFetchEscalationConfigValue) => {
  configStore.set(key, value);
});
const getConfigMock = vi.fn((key: string) => configStore.get(key));

const stableT = (key: string) => key;
vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
}));

vi.mock('@/services/config', () => ({
  getConfigSyncManager: () => ({
    get: getConfigMock,
    set: setConfigMock,
  }),
}));

const toastMock = vi.fn();
vi.mock('@/hooks/shared/useToast', () => ({
  toast: (...args: unknown[]) => toastMock(...args),
}));

const apiRequestMock = vi.fn();
vi.mock('@/lib/api', () => ({
  apiRequest: (...args: unknown[]) => apiRequestMock(...args),
}));

describe('WebFetchEscalationCard', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    configStore.clear();
    setConfigMock.mockClear();
    getConfigMock.mockClear();
    toastMock.mockClear();
    apiRequestMock.mockReset();
    apiRequestMock.mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('persists concurrent edits to independent fields without cancelling prior debounce timers', () => {
    render(<WebFetchEscalationCard />);

    fireEvent.click(screen.getByText('title'));

    const jinaInput = screen.getByPlaceholderText('jinaApiKeyPlaceholder');
    const baseInput = screen.getByPlaceholderText('https://api.firecrawl.dev');

    fireEvent.change(jinaInput, { target: { value: 'jina_live_key' } });
    act(() => {
      vi.advanceTimersByTime(200);
    });
    fireEvent.change(baseInput, { target: { value: 'https://fc.corp.example' } });

    act(() => {
      vi.advanceTimersByTime(600);
    });

    expect(setConfigMock).toHaveBeenCalledTimes(2);
    const saved = configStore.get('webFetchEscalation');
    expect(saved?.jinaApiKey).toBe('jina_live_key');
    expect(saved?.firecrawl.apiBase).toBe('https://fc.corp.example');
    expect(saved?.firecrawl.inheritFromSearch).toBe(false);
  });

  it('reveals dedicated Firecrawl key input and disables inherit switch when custom apiBase is entered', async () => {
    render(<WebFetchEscalationCard />);

    fireEvent.click(screen.getByText('title'));

    // Default inheritFromSearch=true hides the dedicated Firecrawl API Key field
    expect(screen.queryByPlaceholderText('firecrawlApiKeyPlaceholder')).toBeNull();

    const baseInput = screen.getByPlaceholderText('https://api.firecrawl.dev');
    fireEvent.change(baseInput, { target: { value: 'https://firecrawl.internal.example' } });

    // Dedicated API Key field is immediately revealed and custom base hint is shown
    const fcKeyInput = screen.getByPlaceholderText('firecrawlApiKeyPlaceholder');
    expect(fcKeyInput).toBeDefined();
    expect(screen.getByText('firecrawlCustomBaseKeyHint')).toBeDefined();

    // Reuse switch is forced off and disabled while custom apiBase is present
    const switches = screen.getAllByRole('switch');
    const inheritSwitch = switches[1];
    expect(inheritSwitch.getAttribute('aria-checked')).toBe('false');
    expect((inheritSwitch as HTMLButtonElement).disabled).toBe(true);

    fireEvent.change(fcKeyInput, { target: { value: 'fc-selfhosted-secret' } });

    const verifyButton = screen.getByText('verifyFirecrawl');
    await act(async () => {
      fireEvent.click(verifyButton);
      await Promise.resolve();
    });

    expect(apiRequestMock).toHaveBeenCalledTimes(1);
    const [url, options] = apiRequestMock.mock.calls[0] as [string, { method: string; body: string }];
    expect(url).toBe('/integrations/web-fetch/verify');
    expect(options.method).toBe('POST');
    expect(JSON.parse(options.body)).toEqual({
      provider: 'firecrawl',
      api_key: 'fc-selfhosted-secret',
      inherit_from_search: false,
      api_base: 'https://firecrawl.internal.example',
      network_isolation_confirmed: false,
    });
  });

  it('loads stored config, toggles switches, updates sessionCap/network isolation, and handles verify/save states', async () => {
    configStore.set('webFetchEscalation', {
      enabled: true,
      jinaApiKey: 'jina_saved',
      firecrawl: {
        inheritFromSearch: true,
        api_key: null,
        apiBase: null,
        networkIsolationConfirmed: false,
      },
      sessionCap: 10,
    });

    const { unmount } = render(<WebFetchEscalationCard />);

    // Expand via chevron button
    fireEvent.click(screen.getByLabelText('title'));
    expect((screen.getByPlaceholderText('jinaApiKeyPlaceholder') as HTMLInputElement).value).toBe('jina_saved');

    // Toggle top-level enabled switch and Firecrawl inherit switch
    const switches = screen.getAllByRole('switch');
    fireEvent.click(switches[0]);
    expect(configStore.get('webFetchEscalation')?.enabled).toBe(false);

    fireEvent.click(switches[1]);
    expect(configStore.get('webFetchEscalation')?.firecrawl.inheritFromSearch).toBe(false);

    // Edit dedicated Firecrawl key + custom apiBase + check networkIsolationConfirmed
    const fcKeyInput = screen.getByPlaceholderText('firecrawlApiKeyPlaceholder');
    fireEvent.change(fcKeyInput, { target: { value: 'fc-custom-key' } });
    const baseInput = screen.getByPlaceholderText('https://api.firecrawl.dev');
    fireEvent.change(baseInput, { target: { value: 'http://127.0.0.1:3002' } });
    const isolationCheckbox = screen.getByRole('checkbox');
    fireEvent.click(isolationCheckbox);
    expect(configStore.get('webFetchEscalation')?.firecrawl.networkIsolationConfirmed).toBe(true);

    // Clear custom apiBase resets networkIsolationConfirmed after debounce
    fireEvent.change(baseInput, { target: { value: '   ' } });
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(configStore.get('webFetchEscalation')?.firecrawl.apiBase).toBeNull();
    expect(configStore.get('webFetchEscalation')?.firecrawl.networkIsolationConfirmed).toBe(false);

    // Session cap clamping (1..50)
    const capInput = screen.getByRole('spinbutton');
    fireEvent.change(capInput, { target: { value: '99' } });
    expect(configStore.get('webFetchEscalation')?.sessionCap).toBe(50);

    // Verify Jina success + failure toasts
    await act(async () => {
      fireEvent.click(screen.getByText('verifyJina'));
      await Promise.resolve();
    });
    expect(toastMock).toHaveBeenCalledWith({ title: 'verifySuccess' });

    apiRequestMock.mockRejectedValueOnce(new Error('invalid jina key'));
    await act(async () => {
      fireEvent.click(screen.getByText('verifyJina'));
      await Promise.resolve();
    });
    expect(toastMock).toHaveBeenCalledWith({
      title: 'verifyFailed',
      description: 'invalid jina key',
      variant: 'destructive',
    });

    // Save error triggers destructive toast
    setConfigMock.mockImplementationOnce(() => {
      throw new Error('storage full');
    });
    fireEvent.click(switches[0]);
    expect(toastMock).toHaveBeenCalledWith({ title: 'saveFailed', variant: 'destructive' });

    // Pending debounce timer is cleared on unmount
    const callsBeforeUnmount = setConfigMock.mock.calls.length;
    fireEvent.change(screen.getByPlaceholderText('jinaApiKeyPlaceholder'), { target: { value: 'unsaved' } });
    unmount();
    act(() => {
      vi.advanceTimersByTime(600);
    });
    expect(setConfigMock.mock.calls.length).toBe(callsBeforeUnmount);
  });

  it('falls back cleanly to defaults when config storage throws on mount', () => {
    getConfigMock.mockImplementationOnce(() => {
      throw new Error('not initialized');
    });
    render(<WebFetchEscalationCard />);
    expect(screen.getByText('title')).toBeDefined();
  });
});
