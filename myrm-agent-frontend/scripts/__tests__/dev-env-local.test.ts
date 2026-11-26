// @vitest-environment node
import fs from 'fs';
import os from 'os';
import path from 'path';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { ENV_LOCAL_HEADER, syncDevEnvLocal } from '../dev-env-local';

let dir: string;
let file: string;

beforeEach(() => {
  dir = fs.mkdtempSync(path.join(os.tmpdir(), 'dev-env-local-'));
  file = path.join(dir, '.env.local');
});

afterEach(() => {
  fs.rmSync(dir, { recursive: true, force: true });
});

const managed = (apiPort: string) => `${ENV_LOCAL_HEADER}API_PORT=${apiPort}\nNEXT_PUBLIC_DEPLOY_MODE=local\n`;

describe('syncDevEnvLocal when the file is missing', () => {
  it.each([false, true])('seeds the split-dev defaults (lane: %s)', (isLane) => {
    expect(syncDevEnvLocal(file, '18082', isLane)).toBe(`📝 Created ${file} with API_PORT=18082`);
    expect(fs.readFileSync(file, 'utf8')).toBe(managed('18082'));
  });
});

describe('syncDevEnvLocal for the default dev server', () => {
  it('moves the managed API_PORT to its own and reports it', () => {
    fs.writeFileSync(file, managed('8080'));
    expect(syncDevEnvLocal(file, '28080', false)).toBe(`📝 Updated ${file} with API_PORT=28080`);
    expect(fs.readFileSync(file, 'utf8')).toBe(managed('28080'));
  });

  it('leaves a managed file that already carries its API_PORT', () => {
    fs.writeFileSync(file, managed('28080'));
    expect(syncDevEnvLocal(file, '28080', false)).toBeNull();
    expect(fs.readFileSync(file, 'utf8')).toBe(managed('28080'));
  });

  it('appends API_PORT to a hand-written file that has none, and never touches one that has it', () => {
    fs.writeFileSync(file, 'NEXT_PUBLIC_DEPLOY_MODE=sandbox\n');
    expect(syncDevEnvLocal(file, '28080', false)).toBe(`📝 Appended API_PORT=28080 to ${file}`);
    expect(fs.readFileSync(file, 'utf8')).toBe('NEXT_PUBLIC_DEPLOY_MODE=sandbox\n\nAPI_PORT=28080\n');

    expect(syncDevEnvLocal(file, '9999', false)).toBeNull();
    expect(fs.readFileSync(file, 'utf8')).toBe('NEXT_PUBLIC_DEPLOY_MODE=sandbox\n\nAPI_PORT=28080\n');
  });
});

describe('syncDevEnvLocal for a lane', () => {
  // A rewrite makes every dev server running from this directory reload its env and refresh the open pages.
  it.each([
    ['a managed file with another API_PORT', managed('18082')],
    ['a hand-written file without API_PORT', 'NEXT_PUBLIC_DEPLOY_MODE=sandbox\n'],
  ])('leaves %s as it is, to the byte and to the modification time', (_label, content) => {
    fs.writeFileSync(file, content);
    const past = new Date('2026-01-01T00:00:00Z');
    fs.utimesSync(file, past, past);

    expect(syncDevEnvLocal(file, '18083', true)).toBeNull();

    expect(fs.readFileSync(file, 'utf8')).toBe(content);
    expect(fs.statSync(file).mtime).toEqual(past);
  });

  it('does not overwrite a file another lane created first', () => {
    expect(syncDevEnvLocal(file, '18081', true)).toBe(`📝 Created ${file} with API_PORT=18081`);
    expect(syncDevEnvLocal(file, '18082', true)).toBeNull();
    expect(fs.readFileSync(file, 'utf8')).toBe(managed('18081'));
  });
});
