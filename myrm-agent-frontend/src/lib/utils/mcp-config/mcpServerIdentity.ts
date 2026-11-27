/**
 * [INPUT]
 * - (no imports: pure functions)
 *
 * [OUTPUT]
 * - mcpServerIdentity: the `{server}` segment of `mcp__{server}__{tool}` that a server name stands for
 * - findSameMcpServer: the first of some servers whose tools would share the prefix of a given name
 * - partitionByMcpIdentity: splits incoming servers into those that fit beside the configured ones and those that do not
 * - uniqueMcpName: a free variant of a name (`name`, `name-2`, `name-3`, ...)
 *
 * [POS]
 * Frontend twin of the server identity rule in `myrm_agent_harness/toolkits/mcp/server_identity.py`. Two servers with
 * one identity cannot run side by side, so every place that adds a server asks here first. Both sides read the vectors
 * in `shared/config/mcp_server_identity.json`: a rule changed on one side turns the other side's test red. A name that
 * stacks more than 30 combining marks (never natural text) gets another hash segment on the server, which normalises
 * it in Stream-Safe form; the server decides.
 */

const FNV_OFFSET_BASIS = 0x811c9dc5;
const FNV_PRIME = 0x01000193;
const SUFFIX_HEX_DIGITS = 6;
const ASCII_ONLY = /^\p{ASCII}*$/u;

/** Characters outside `[A-Za-z0-9_]` become `_`, `_` runs collapse and the ends are trimmed. */
const fold = (value: string): string =>
  value
    .replace(/[^A-Za-z0-9_]/g, '_')
    .replace(/_{2,}/g, '_')
    .replace(/^_+|_+$/g, '');

/** First six hex digits of the 32-bit FNV-1a hash of the UTF-8 bytes (a lone surrogate encodes as U+FFFD). */
const stableSuffix = (text: string): string => {
  let digest = FNV_OFFSET_BASIS;
  for (const byte of new TextEncoder().encode(text)) {
    digest = Math.imul(digest ^ byte, FNV_PRIME) >>> 0;
  }
  return digest.toString(16).padStart(8, '0').slice(0, SUFFIX_HEX_DIGITS);
};

/**
 * An ASCII name that folds to something keeps the folded form (`my-api` is `my_api`); any other name is
 * `{folded or "unnamed"}_{hash}`, so `天气` and `我的服务` are told apart. The hash only tells names apart:
 * whoever adds a server checks for a taken identity, and a collision is rejected there.
 */
export const mcpServerIdentity = (name: string): string => {
  const normalized = name.normalize('NFC');
  const folded = fold(normalized);
  if (folded && ASCII_ONLY.test(normalized)) {
    return folded;
  }
  return `${folded || 'unnamed'}_${stableSuffix(normalized)}`;
};

/** The first of `servers` whose tools would share the prefix of `name`, if there is one. */
export const findSameMcpServer = <T extends { name: string }>(servers: readonly T[], name: string): T | undefined => {
  const identity = mcpServerIdentity(name);
  return servers.find((server) => mcpServerIdentity(server.name) === identity);
};

/** Keeps incoming servers in order; one that meets a taken identity (configured, or accepted just before) is skipped. */
export const partitionByMcpIdentity = <T extends { name: string }>(
  incoming: readonly T[],
  existingNames: readonly string[],
): { accepted: T[]; skipped: T[] } => {
  const taken = new Set(existingNames.map(mcpServerIdentity));
  const accepted: T[] = [];
  const skipped: T[] = [];
  for (const server of incoming) {
    const identity = mcpServerIdentity(server.name);
    if (taken.has(identity)) {
      skipped.push(server);
    } else {
      taken.add(identity);
      accepted.push(server);
    }
  }
  return { accepted, skipped };
};

export const uniqueMcpName = (name: string, existingNames: readonly string[]): string => {
  const taken = new Set(existingNames.map(mcpServerIdentity));
  let candidate = name;
  for (let suffix = 2; taken.has(mcpServerIdentity(candidate)); suffix += 1) {
    candidate = `${name}-${suffix}`;
  }
  return candidate;
};
