/** @vitest-environment jsdom */
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { SearchDialog, SearchTrigger } from '../search-dialog';
import type { SearchResult } from '@/services/chatHistorySearch';

const mockSearchChatHistory = vi.hoisted(() => vi.fn());
const mockOnOpenChange = vi.hoisted(() => vi.fn());
const mockChatState = vi.hoisted(() => ({ chatId: 'current-chat-id', addMentionReference: vi.fn() }));

vi.mock('next/navigation', () => ({
  useRouter: () => ({ push: vi.fn() }),
}));

const stableT = (key: string) => key;

vi.mock('next-intl', () => ({
  useTranslations: () => stableT,
}));

vi.mock('@/services/chatHistorySearch', () => ({
  searchChatHistory: (...args: unknown[]) => mockSearchChatHistory(...args),
}));

vi.mock('@/store/useChatStore', () => ({
  default: Object.assign((selector: (state: typeof mockChatState) => unknown) => selector(mockChatState), {
    getState: () => mockChatState,
  }),
}));

const item: SearchResult = {
  id: '42',
  chat_id: 'other-chat-id',
  chat_title: 'Other chat',
  role: 'assistant',
  content: 'matched content',
  snippet: 'matched snippet',
  sent_at: '2026-08-04T10:00:00.000Z',
};

/** The search debounce is 300 ms; a loaded machine needs more than waitFor's default second. */
const SLOW = { timeout: 3000 };

function renderDialog({ open = true, children = <div /> }: { open?: boolean; children?: React.ReactNode } = {}) {
  const dialog = (isOpen: boolean) => (
    <SearchDialog open={isOpen} onOpenChange={mockOnOpenChange}>
      {children}
    </SearchDialog>
  );
  const view = render(dialog(open));
  return { ...view, setOpen: (isOpen: boolean) => view.rerender(dialog(isOpen)) };
}

async function typeQuery(value: string) {
  const input = await screen.findByPlaceholderText('search.placeholder');
  fireEvent.change(input, { target: { value } });
  return input;
}

async function openTimeFilter() {
  fireEvent.click(await screen.findByTitle('search.timeFilter'));
}

function clickPreset(preset: 'Today' | 'Week' | 'Month' | 'Quarter') {
  fireEvent.click(screen.getByRole('button', { name: `search.preset${preset}` }));
}

beforeEach(() => {
  mockSearchChatHistory.mockReset();
  mockOnOpenChange.mockReset();
  mockSearchChatHistory.mockResolvedValue({ items: [item], total: 1 });
});

describe('SearchDialog time filter', () => {
  /** Mar 31 is the day a past month that rolls over instead of stepping back by calendar months goes wrong. */
  const now = new Date(2026, 2, 31, 15, 0, 0, 0);

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(now);
  });

  afterEach(() => {
    vi.useRealTimers();
  });

  it('offers today, the past week, month and quarter, and the way back once one is chosen', async () => {
    renderDialog();
    await openTimeFilter();

    for (const preset of ['Today', 'Week', 'Month', 'Quarter'] as const) {
      expect(screen.getByRole('button', { name: `search.preset${preset}` })).toBeInTheDocument();
    }
    expect(screen.queryByRole('button', { name: 'search.clearTimeFilter' })).toBeNull();

    clickPreset('Week');

    expect(screen.getByRole('button', { name: 'search.clearTimeFilter' })).toBeInTheDocument();
  });

  it('searches the past month from Feb 28 on Mar 31, not from Mar 3', async () => {
    renderDialog();
    await typeQuery('hello');
    await waitFor(() => expect(mockSearchChatHistory).toHaveBeenCalledWith('hello', 20, 0, undefined, undefined), SLOW);

    await openTimeFilter();
    clickPreset('Month');

    await waitFor(
      () =>
        expect(mockSearchChatHistory).toHaveBeenLastCalledWith(
          'hello',
          20,
          0,
          new Date(2026, 1, 28, 15).toISOString(),
          now.toISOString(),
        ),
      SLOW,
    );
  });

  it('applies a preset chosen before typing to the first search', async () => {
    renderDialog();
    await openTimeFilter();
    clickPreset('Week');

    await typeQuery('hello');

    await waitFor(
      () =>
        expect(mockSearchChatHistory).toHaveBeenLastCalledWith(
          'hello',
          20,
          0,
          new Date(2026, 2, 24, 15).toISOString(),
          now.toISOString(),
        ),
      SLOW,
    );
  });

  it.each([
    ['choosing the chosen preset again', () => clickPreset('Month')],
    ['the clear button', () => fireEvent.click(screen.getByRole('button', { name: 'search.clearTimeFilter' }))],
  ])('searches without a time window again after %s', async (_how, clear) => {
    renderDialog();
    await typeQuery('hello');
    await openTimeFilter();
    clickPreset('Month');
    await waitFor(
      () =>
        expect(mockSearchChatHistory).toHaveBeenLastCalledWith('hello', 20, 0, expect.any(String), expect.any(String)),
      SLOW,
    );

    clear();

    await waitFor(
      () => expect(mockSearchChatHistory).toHaveBeenLastCalledWith('hello', 20, 0, undefined, undefined),
      SLOW,
    );
  });
});

describe('SearchDialog shortcut and lifecycle', () => {
  it.each([
    { name: 'Cmd+K opens a closed dialog', open: false, modifier: { metaKey: true }, expected: true },
    { name: 'Ctrl+K opens a closed dialog', open: false, modifier: { ctrlKey: true }, expected: true },
    { name: 'Cmd+K closes an open dialog', open: true, modifier: { metaKey: true }, expected: false },
  ])('$name', ({ open, modifier, expected }) => {
    renderDialog({ open });

    fireEvent.keyDown(window, { key: 'k', ...modifier });

    expect(mockOnOpenChange).toHaveBeenCalledExactlyOnceWith(expected);
  });

  it.each([
    { name: 'a plain k', event: { key: 'k' } },
    { name: 'Cmd+J', event: { key: 'j', metaKey: true } },
  ])('leaves $name to the page', ({ event }) => {
    renderDialog({ open: false });

    fireEvent.keyDown(window, event);

    expect(mockOnOpenChange).not.toHaveBeenCalled();
  });

  it('locks the page scroll while open, and reopens empty after a close', async () => {
    const { setOpen } = renderDialog();
    await typeQuery('hello');
    await openTimeFilter();
    clickPreset('Week');
    await screen.findByRole('button', { name: /Other chat/ });
    expect(document.body.style.overflow).toBe('hidden');

    setOpen(false);
    expect(document.body.style.overflow).toBe('');
    setOpen(true);

    expect(await screen.findByPlaceholderText('search.placeholder')).toHaveValue('');
    expect(screen.getByText('search.hint')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'search.presetWeek' })).toBeNull();
  });

  it('empties the query and its results with the clear button', async () => {
    renderDialog();
    const input = await typeQuery('hello');
    await screen.findByRole('button', { name: /Other chat/ });

    // The first control of the input row is the clear button; the time filter button follows it.
    fireEvent.click(within(input.parentElement as HTMLElement).getAllByRole('button')[0]);

    expect(input).toHaveValue('');
    expect(screen.getByText('search.hint')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /Other chat/ })).toBeNull();
  });

  it('returns focus to the composer after a result is cited into it', async () => {
    renderDialog({ children: <textarea data-chat-input aria-label="composer" /> });
    await typeQuery('hello');

    fireEvent.click((await screen.findAllByRole('button', { name: 'search.citeToComposer' }))[0]);

    await waitFor(() => expect(screen.getByLabelText('composer')).toHaveFocus(), SLOW);
  });
});

describe('SearchDialog results area', () => {
  it('shows the searching indicator while the request is pending', async () => {
    mockSearchChatHistory.mockReturnValue(new Promise(() => undefined));
    renderDialog();

    await typeQuery('hello');

    expect(await screen.findByText('search.searching', undefined, SLOW)).toBeInTheDocument();
  });

  it('counts the matches in the footer', async () => {
    mockSearchChatHistory.mockResolvedValue({ items: [item], total: 7 });
    renderDialog();

    await typeQuery('hello');

    expect(await screen.findByText('search.resultsCount', undefined, SLOW)).toBeInTheDocument();
  });

  it.each([
    ['an empty answer', () => mockSearchChatHistory.mockResolvedValue({ items: [], total: 0 })],
    ['a failed request', () => mockSearchChatHistory.mockRejectedValue(new Error('offline'))],
  ])('says nothing matched after %s', async (_case, arrange) => {
    arrange();
    renderDialog();

    await typeQuery('hello');

    expect(await screen.findByText('search.noResults', undefined, SLOW)).toBeInTheDocument();
  });

  it('does not search for a blank query', async () => {
    renderDialog();

    await typeQuery('   ');
    await new Promise((resolve) => setTimeout(resolve, 400));

    expect(mockSearchChatHistory).not.toHaveBeenCalled();
    expect(screen.getByText('search.hint')).toBeInTheDocument();
  });

  it('searches once for a burst of keystrokes, with the text as it ended', async () => {
    renderDialog();
    const input = await typeQuery('h');
    fireEvent.change(input, { target: { value: 'he' } });
    fireEvent.change(input, { target: { value: 'hello' } });

    await waitFor(() => expect(mockSearchChatHistory).toHaveBeenCalledWith('hello', 20, 0, undefined, undefined), SLOW);

    expect(mockSearchChatHistory).toHaveBeenCalledTimes(1);
  });
});

describe('SearchTrigger', () => {
  it('shows the search placeholder unless given its own text', () => {
    const { rerender } = render(<SearchTrigger />);
    expect(screen.getByRole('button')).toHaveTextContent('search.placeholder');

    rerender(<SearchTrigger placeholder="Find chats" />);

    expect(screen.getByRole('button')).toHaveTextContent('Find chats');
  });

  it('opens the dialog when pressed', () => {
    const onOpenDialog = vi.fn();
    render(<SearchTrigger onOpenDialog={onOpenDialog} />);

    fireEvent.click(screen.getByRole('button'));

    expect(onOpenDialog).toHaveBeenCalledTimes(1);
  });
});
