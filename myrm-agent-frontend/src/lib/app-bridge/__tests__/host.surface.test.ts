import { describe, expect, it } from 'vitest';
import type { CapabilityStatus } from '../declaration';
import { reasonOf, request, setup, surfaceCalls } from './hostHarness';

describe('outside the app’s own desktop window', () => {
  it.each([
    ['tray.set', { title: 'x' }],
    ['tray.clear', {}],
    ['shortcut.register', { accelerator: 'Alt+F1' }],
    ['shortcut.unregister', { accelerator: 'Alt+F1' }],
    ['float.set', { on: true }],
  ])('answers %s with unsupported and logs nothing', async (method, params) => {
    const { handle, activity, surface } = setup({}, { desktop: false });

    expect(reasonOf(await handle(request(method, params)))).toBe('unsupported');
    expect(activity).toEqual([]);
    expect(surfaceCalls(surface)).toEqual([]);
  });

  it('still serves the capabilities that do not need the desktop', async () => {
    const { handle, showNotification } = setup({}, { desktop: false });

    expect(await handle(request('notify', { title: 't' }))).toMatchObject({ result: null });
    expect(showNotification).toHaveBeenCalledTimes(1);
  });
});

describe('every desktop capability needs the user’s allowance', () => {
  it.each<[string, unknown, 'tray' | 'shortcut' | 'float', string | null]>([
    ['tray.set', { title: 'x' }, 'tray', null],
    ['tray.clear', {}, 'tray', null],
    ['shortcut.register', { accelerator: 'Alt+F1' }, 'shortcut', 'Alt+F1'],
    ['float.set', { on: true }, 'float', 'on'],
  ])('%s is refused until allowed, with the reason', async (method, params, capability, detail) => {
    const cases: [CapabilityStatus, string][] = [
      ['ask', 'permission_denied'],
      ['denied', 'permission_denied'],
      ['undeclared', 'not_declared'],
    ];
    for (const [status, reason] of cases) {
      const { handle, activity, surface } = setup({ [capability]: status });

      expect(reasonOf(await handle(request(method, params)))).toBe(reason);
      expect(activity).toEqual([{ capability, outcome: 'denied', detail }]);
      expect(surfaceCalls(surface)).toEqual([]);
    }
  });
});

describe('tray', () => {
  it('sets the item from the title and tooltip and logs it', async () => {
    const { handle, surface, activity } = setup();

    const reply = await handle(request('tray.set', { title: ' 3 unread ', tooltip: 'Inbox' }));

    expect(reply).toEqual({ jsonrpc: '2.0', id: 1, result: null });
    expect(surface.setTray).toHaveBeenCalledWith('3 unread', 'Inbox');
    expect(activity).toEqual([{ capability: 'tray', outcome: 'ok', detail: null }]);
  });

  it.each([{}, { title: '' }, { title: 'x'.repeat(25) }, { tooltip: 'x'.repeat(81) }, { title: 3 }, 'x'])(
    'rejects %j without touching the desktop',
    async (params) => {
      const { handle, surface } = setup();

      expect(reasonOf(await handle(request('tray.set', params)))).toBe('invalid_params');
      expect(surface.setTray).not.toHaveBeenCalled();
    },
  );

  it('reports a shell that refused as failed', async () => {
    const { handle, surface, activity } = setup();
    surface.setTray.mockResolvedValue(false);

    expect(reasonOf(await handle(request('tray.set', { title: 'x' })))).toBe('failed');
    expect(activity).toEqual([{ capability: 'tray', outcome: 'error', detail: null }]);
  });

  it('removes the item on tray.clear', async () => {
    const { handle, surface } = setup();

    expect(await handle(request('tray.clear'))).toMatchObject({ result: null });
    expect(surface.clearTray).toHaveBeenCalledTimes(1);
  });

  it('allows thirty calls a minute', async () => {
    const { handle, surface } = setup();
    for (let index = 0; index < 30; index += 1) {
      await handle(request('tray.set', { title: `n${index}` }, index));
    }

    expect(reasonOf(await handle(request('tray.set', { title: 'x' }, 99)))).toBe('rate_limited');
    expect(surface.setTray).toHaveBeenCalledTimes(30);
  });
});

describe('shortcut', () => {
  it('registers a declared combination, whichever way the page spells it', async () => {
    const { handle, surface, activity } = setup();

    const reply = await handle(request('shortcut.register', { accelerator: 'shift+commandorcontrol+k' }));

    expect(reply).toMatchObject({ result: null });
    expect(surface.registerShortcut).toHaveBeenCalledWith('CommandOrControl+Shift+K');
    expect(activity).toEqual([{ capability: 'shortcut', outcome: 'ok', detail: 'CommandOrControl+Shift+K' }]);
  });

  it('refuses a combination the user was not shown', async () => {
    const { handle, surface, activity } = setup();

    expect(reasonOf(await handle(request('shortcut.register', { accelerator: 'Alt+F9' })))).toBe('shortcut_not_granted');
    expect(surface.registerShortcut).not.toHaveBeenCalled();
    expect(activity).toEqual([{ capability: 'shortcut', outcome: 'denied', detail: 'Alt+F9' }]);
  });

  it.each(['K', 'Shift+K', 'Super+K', 42, null])('rejects the unsupported combination %j', async (accelerator) => {
    const { handle, surface } = setup();

    expect(reasonOf(await handle(request('shortcut.register', { accelerator })))).toBe('invalid_params');
    expect(surface.registerShortcut).not.toHaveBeenCalled();
  });

  it('tells the page when the combination is taken and when the shell failed', async () => {
    const { handle, surface } = setup();

    surface.registerShortcut.mockResolvedValue('conflict');
    expect(reasonOf(await handle(request('shortcut.register', { accelerator: 'Alt+F1' })))).toBe('shortcut_conflict');

    surface.registerShortcut.mockResolvedValue('failed');
    expect(reasonOf(await handle(request('shortcut.register', { accelerator: 'Alt+F1' })))).toBe('failed');
  });

  it('releases one combination on shortcut.unregister', async () => {
    const { handle, surface } = setup();

    expect(await handle(request('shortcut.unregister', { accelerator: 'alt+f1' }))).toMatchObject({ result: null });
    expect(surface.unregisterShortcut).toHaveBeenCalledWith('Alt+F1');
  });
});

describe('float', () => {
  it.each([true, false])('keeps the window on top: %s', async (on) => {
    const { handle, surface, activity } = setup();

    expect(await handle(request('float.set', { on }))).toMatchObject({ result: null });
    expect(surface.setFloat).toHaveBeenCalledWith(on);
    expect(activity).toEqual([{ capability: 'float', outcome: 'ok', detail: on ? 'on' : 'off' }]);
  });

  it.each([{}, { on: 'yes' }, null])('rejects %j', async (params) => {
    const { handle, surface } = setup();

    expect(reasonOf(await handle(request('float.set', params)))).toBe('invalid_params');
    expect(surface.setFloat).not.toHaveBeenCalled();
  });
});

describe('taking back what the page set up', () => {
  it('clears the tray item once, and only if the page set one', async () => {
    const { handle, host, surface } = setup();

    await host.revoke('tray');
    expect(surface.clearTray).not.toHaveBeenCalled();

    await handle(request('tray.set', { title: 'x' }));
    await host.revoke('tray');
    await host.revoke('tray');
    expect(surface.clearTray).toHaveBeenCalledTimes(1);
  });

  it('releases every combination of the app when shortcut is revoked', async () => {
    const { handle, host, surface } = setup();
    await handle(request('shortcut.register', { accelerator: 'Alt+F1' }));

    await host.revoke('shortcut');

    expect(surface.unregisterShortcut).toHaveBeenCalledWith(null);
  });

  it('puts the window back when float is revoked', async () => {
    const { handle, host, surface } = setup();
    await handle(request('float.set', { on: true }));

    await host.revoke('float');

    expect(surface.setFloat).toHaveBeenLastCalledWith(false);
  });

  it('takes everything down when the page goes away', async () => {
    const { handle, host, surface } = setup();
    await handle(request('tray.set', { title: 'x' }));
    await handle(request('shortcut.register', { accelerator: 'Alt+F1' }));
    await handle(request('float.set', { on: true }));

    await host.dispose();

    expect(surface.clearTray).toHaveBeenCalledTimes(1);
    expect(surface.unregisterShortcut).toHaveBeenCalledWith(null);
    expect(surface.setFloat).toHaveBeenLastCalledWith(false);
  });

  it('does not leave behind what a call set up while the user revoked it', async () => {
    const { handle, current, surface } = setup();
    surface.setTray.mockImplementation(async () => {
      current.tray = 'denied';
      return true;
    });

    await handle(request('tray.set', { title: 'x' }));

    expect(surface.clearTray).toHaveBeenCalledTimes(1);
  });

  it('keeps going when the shell refuses one step', async () => {
    const { handle, host, surface } = setup();
    await handle(request('tray.set', { title: 'x' }));
    await handle(request('float.set', { on: true }));
    surface.clearTray.mockRejectedValue(new Error('gone'));

    await host.dispose();

    expect(surface.setFloat).toHaveBeenLastCalledWith(false);
  });
});
