/** @vitest-environment jsdom */

/**
 * [INPUT]
 * - ../useLazyText::useLazyText
 *
 * [OUTPUT]
 * - Tests: when the text is loaded, when a failed load is retried, and what survives turning the consumer off.
 *
 * [POS]
 * The loading rules the artifact card's inline preview and organize-plan panel both rely on.
 */

import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useLazyText } from '../useLazyText';

function mount(initialEnabled: boolean, load: () => Promise<string | null>) {
  return renderHook(({ enabled }) => useLazyText(enabled, load), { initialProps: { enabled: initialEnabled } });
}

describe('useLazyText', () => {
  it('does not load while disabled', () => {
    const load = vi.fn().mockResolvedValue('body');
    const view = mount(false, load);

    expect(load).not.toHaveBeenCalled();
    expect(view.result.current).toMatchObject({ text: null, loading: false });
  });

  it('loads once when enabled and then stops loading', async () => {
    const load = vi.fn().mockResolvedValue('body');
    const view = mount(true, load);

    expect(view.result.current.loading).toBe(true);
    await waitFor(() => expect(view.result.current.text).toBe('body'));
    expect(view.result.current.loading).toBe(false);
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('keeps loaded text when turned off and on, without loading again', async () => {
    const load = vi.fn().mockResolvedValue('body');
    const view = mount(true, load);
    await waitFor(() => expect(view.result.current.text).toBe('body'));

    view.rerender({ enabled: false });
    view.rerender({ enabled: true });

    expect(view.result.current.text).toBe('body');
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('does not retry a failed load while still enabled', async () => {
    const load = vi.fn().mockResolvedValue(null);
    const view = mount(true, load);
    await waitFor(() => expect(view.result.current.loading).toBe(false));

    view.rerender({ enabled: true });

    expect(view.result.current.text).toBeNull();
    expect(load).toHaveBeenCalledTimes(1);
  });

  it('retries a failed load after being turned off and on', async () => {
    const load = vi.fn().mockResolvedValueOnce(null).mockResolvedValueOnce('second try');
    const view = mount(true, load);
    await waitFor(() => expect(view.result.current.loading).toBe(false));

    view.rerender({ enabled: false });
    view.rerender({ enabled: true });

    await waitFor(() => expect(view.result.current.text).toBe('second try'));
    expect(load).toHaveBeenCalledTimes(2);
  });

  it('drops a result that arrives after being turned off', async () => {
    let resolve: (value: string) => void = () => {};
    const load = vi.fn(() => new Promise<string>((done) => (resolve = done)));
    const view = mount(true, load);

    view.rerender({ enabled: false });
    await act(async () => resolve('late'));

    expect(view.result.current.text).toBeNull();
  });

  it('lets the consumer replace the text', async () => {
    const view = mount(true, vi.fn().mockResolvedValue('body'));
    await waitFor(() => expect(view.result.current.text).toBe('body'));

    act(() => view.result.current.setText('edited'));

    expect(view.result.current.text).toBe('edited');
  });
});
