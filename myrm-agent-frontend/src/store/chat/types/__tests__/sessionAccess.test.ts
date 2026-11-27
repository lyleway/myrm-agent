import { describe, expect, it } from 'vitest';

import { normalizeSessionAccessRoots } from '../sessionAccess';

describe('normalizeSessionAccessRoots', () => {
  it('reads the roots the server stores', () => {
    expect(
      normalizeSessionAccessRoots([
        { path: '/data', writable: true, label: 'Data', source: 'hitl_grant' },
        { path: '/docs', writable: 'yes' },
      ]),
    ).toEqual([
      { path: '/data', writable: true, label: 'Data', source: 'hitl_grant' },
      { path: '/docs', writable: false, label: undefined, source: undefined },
    ]);
  });

  it('keeps a root path exactly as stored, since a folder name that ends in a space is another folder', () => {
    // Revoking sends this text back; a trimmed copy would name the folder above it and revoke nothing.
    const [root] = normalizeSessionAccessRoots([{ path: '/data/shared ', writable: true }]);

    expect(root.path).toBe('/data/shared ');
  });

  it('trims the display text but not the path', () => {
    const [root] = normalizeSessionAccessRoots([
      { path: '/data/shared ', writable: true, label: '  Data ', source: ' x ' },
    ]);

    expect(root).toEqual({ path: '/data/shared ', writable: true, label: 'Data', source: 'x' });
  });

  it('drops entries without a usable path and anything that is not a list', () => {
    expect(normalizeSessionAccessRoots([null, 'x', { path: 7 }, { path: '   ' }, { writable: true }])).toEqual([]);
    expect(normalizeSessionAccessRoots({ path: '/data' })).toEqual([]);
  });
});
