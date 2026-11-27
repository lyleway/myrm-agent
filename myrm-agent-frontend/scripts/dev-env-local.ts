/**
 * [INPUT]
 * - fs (POS: reads and writes the `.env.local` the dev entry maintains)
 *
 * [OUTPUT]
 * - ENV_LOCAL_HEADER: first line marking the file as maintained by `bun run dev`
 * - syncDevEnvLocal: seeds the split-dev defaults, and keeps `API_PORT` in step for the default dev server
 *
 * [POS]
 * `.env.local` upkeep of `bun run dev`. Every dev server started from one directory shares the file, and Next reloads the
 * env of every running server when it changes: the pages open on those servers re-fetch their server components, and an
 * open dialog in such a page can disappear. A lane (an isolated runtime) gets its API port from its own environment,
 * which outranks the file, so it may create the file but never rewrites it.
 */
import fs from 'fs';

export const ENV_LOCAL_HEADER = '# Auto-managed by bun run dev — local split-dev defaults\n';

/** Applies the upkeep to `file` and returns the line to report, or null when the file was left as it was. */
export function syncDevEnvLocal(file: string, apiPort: string, isLane: boolean): string | null {
  const apiLine = `API_PORT=${apiPort}`;
  try {
    // 'wx': lanes starting together on a fresh checkout must not overwrite each other's file.
    fs.writeFileSync(file, `${ENV_LOCAL_HEADER}${apiLine}\nNEXT_PUBLIC_DEPLOY_MODE=local\n`, {
      encoding: 'utf8',
      flag: 'wx',
    });
    return `📝 Created ${file} with ${apiLine}`;
  } catch (error) {
    if ((error as NodeJS.ErrnoException).code !== 'EEXIST') {
      throw error;
    }
  }
  if (isLane) {
    return null;
  }
  const content = fs.readFileSync(file, 'utf8');
  if (content.startsWith(ENV_LOCAL_HEADER)) {
    const updated = content.replace(/^API_PORT\s*=.*$/m, apiLine);
    if (updated === content) {
      return null;
    }
    fs.writeFileSync(file, updated, 'utf8');
    return `📝 Updated ${file} with ${apiLine}`;
  }
  if (/\bAPI_PORT\s*=/.test(content)) {
    return null;
  }
  fs.appendFileSync(file, `\n${apiLine}\n`, 'utf8');
  return `📝 Appended ${apiLine} to ${file}`;
}
