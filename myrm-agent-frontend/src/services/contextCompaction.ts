/**
 * [INPUT]
 * - @/lib/api::apiRequest, ApiError (POS: 带鉴权与错误归一的 API 请求；`silent` 把失败交给调用方)
 * - @/lib/constants/compact-focus-topic::COMPACT_FOCUS_TOPIC_MAX_LENGTH (POS: 服务端接受的聚焦主题最大长度)
 * - @/lib/utils/firstChars::firstChars (POS: 按字符数截断文本，不拆开一个字符)
 * - @/store/useChatStore::refreshCompactionState (POS: 会话状态 store，压缩落库后的吸收入口；首次压缩时才加载)
 *
 * [OUTPUT]
 * - CompactResult: 一次手动压缩上下文的结果
 * - compactChat: 压缩会话上下文，可带聚焦主题；先让服务端受理并取得运行 id，再按间隔询问这个运行到它给出结果
 * - COMPACT_POLL_FIRST_INTERVAL_MS / COMPACT_POLL_MAX_INTERVAL_MS / COMPACT_POLL_OUTAGE_LIMIT_MS / COMPACT_RUN_DEADLINE_MS: 轮询的间隔、服务端不可达的容忍时长与页面等一次压缩的期限
 * - runManualCompaction: 手动压缩，并把省下 token 的结果交给 store；输入框、`/compact`、策略面板三个入口共用
 * - CompactNotice / describeCompactSkip / COMPACT_FAILED_NOTICE: 服务端没有压缩时展示给用户的提示（文案键 + 严重度）
 * - DONT_REMIND_COMPACT_STORAGE_KEY: 「手动压缩前不再提醒」偏好的 localStorage 键
 *
 * [POS]
 * 上下文压缩的前端服务。聚焦主题发出前按服务端上限截断，所以输入框与 `/compact <主题>` 两条路径都不会被服务端拒收；
 * 压缩后的 store 同步与「没压缩」的措辞各只有一处，三个入口因此对同一次压缩给出同一个结果；
 * 压缩由模型摘要整段历史，耗时远超一个请求能在代理链上存活的时间（本地 Next 重写默认 30 秒，运营方的 Nginx 或负载均衡默认 60 秒）：
 * 请求只让服务端受理并立刻返回运行 id，页面再按这个 id 询问运行状态，直到服务端自己给出结果，不在服务端仍在压缩时先报「失败」；
 * 询问的是自己受理的那次运行，所以同一聊天另一个页面随后发起的压缩不会把这次的结果顶掉。
 */

import { ApiError, apiRequest } from '@/lib/api';
import { COMPACT_FOCUS_TOPIC_MAX_LENGTH } from '@/lib/constants/compact-focus-topic';
import { firstChars } from '@/lib/utils/firstChars';

export interface CompactResult {
  compacted: boolean;
  message_count: number;
  tokens_saved: number;
  reason: string | null;
  focus_topic?: string;
  backup_path?: string | null;
}

/** Set once the user ticks "don't remind me" in the input box dialog; `/compact` reads it to skip its prompt-cache note. */
export const DONT_REMIND_COMPACT_STORAGE_KEY = 'dontRemindCompact';

/**
 * A compaction has a model summarise the whole history, which can take minutes. The server ends one after 90 s without
 * progress or 600 s in all and records the reason, so the page waits slightly longer than that: what the user reads
 * is the server's answer, not the page's deadline running out while the server is still compacting.
 */
export const COMPACT_RUN_DEADLINE_MS = 11 * 60 * 1000;

/** A compaction that has nothing to do ends at once, so the first look comes early; a long one is asked about less eagerly. */
export const COMPACT_POLL_FIRST_INTERVAL_MS = 300;
export const COMPACT_POLL_MAX_INTERVAL_MS = 2000;
const COMPACT_POLL_INTERVAL_GROWTH = 1.5;
/**
 * How long the polls may keep failing before the server counts as gone. A failed poll says the server could not be
 * reached (a network switch, a proxy restarting), not that the compaction failed: it goes on without the page. A count of
 * failed polls cannot measure that, because quick failures spend it in seconds while a poll that hangs takes its whole
 * 30 s request timeout to fail. The time is counted from when the first failing poll was asked and starts afresh at the
 * next answer.
 */
export const COMPACT_POLL_OUTAGE_LIMIT_MS = 60_000;

/** What the server answers when it accepts a compaction: the id of the run that does it. */
interface CompactRunStarted {
  run_id: string;
}

/** What the server says of a compaction run; a failed one carries the failure in the terms of an HTTP error. */
type CompactRun =
  | { status: 'running' }
  | { status: 'succeeded'; result: CompactResult }
  | { status: 'failed'; error: { http_status: number; code: number; message: string } };

const sleep = (ms: number): Promise<void> =>
  new Promise((resolve) => {
    setTimeout(resolve, ms);
  });

const pollInterval = (attempt: number): number =>
  Math.min(
    COMPACT_POLL_MAX_INTERVAL_MS,
    Math.round(COMPACT_POLL_FIRST_INTERVAL_MS * COMPACT_POLL_INTERVAL_GROWTH ** attempt),
  );

/**
 * Asks for the run until it ends. The polls are silent: the default handling would toast every blip with a retry button,
 * and a failed compaction is worded by the caller. A 404 is the server saying it holds no such run (it restarted, or
 * dropped the run among newer ones), which no later poll can change, so it ends the wait at once.
 */
async function awaitCompactRun(chatId: string, runId: string): Promise<CompactResult> {
  const deadline = Date.now() + COMPACT_RUN_DEADLINE_MS;
  // When the first of the polls that have failed since the last answer was asked; null while the server answers.
  let unreachableSince: number | null = null;
  for (let attempt = 0; Date.now() < deadline; attempt += 1) {
    await sleep(pollInterval(attempt));
    const askedAt = Date.now();
    let run: CompactRun;
    try {
      run = await apiRequest<CompactRun>(`/chats/${chatId}/compact/runs/${runId}`, { silent: true });
      unreachableSince = null;
    } catch (error) {
      unreachableSince ??= askedAt;
      if (
        (error instanceof ApiError && error.code === 404) ||
        Date.now() - unreachableSince >= COMPACT_POLL_OUTAGE_LIMIT_MS
      ) {
        throw error;
      }
      continue;
    }
    if (run.status === 'succeeded') {
      return run.result;
    }
    if (run.status === 'failed') {
      // Built here and not raised by `apiRequest`, so no global toast piles up on the failure line each entry words itself.
      const { http_status: httpStatus, code, message } = run.error;
      throw new ApiError(message, httpStatus, [], undefined, String(code));
    }
  }
  throw new Error('The compaction did not finish in time');
}

export const compactChat = async (chatId: string, focusTopic?: string): Promise<CompactResult> => {
  // The server refuses a longer topic (HTTP 422); a topic typed after `/compact` has no box to stop it, so it is cut here.
  const body = focusTopic
    ? JSON.stringify({ focus_topic: firstChars(focusTopic, COMPACT_FOCUS_TOPIC_MAX_LENGTH) })
    : undefined;
  // The server only starts the compaction and answers at once, so this request needs no budget of its own.
  const started = await apiRequest<CompactRunStarted>(`/chats/${chatId}/compact`, {
    method: 'POST',
    ...(body ? { body, headers: { 'Content-Type': 'application/json' } } : {}),
  });
  return awaitCompactRun(chatId, started.run_id);
};

export interface CompactNotice {
  /** Absolute locale key of the line the user reads. */
  messageKey: string;
  severity: 'info' | 'warning' | 'error';
}

const NOTHING_TO_COMPACT: CompactNotice = { messageKey: 'chat.compact.skip.nothing', severity: 'info' };
const ALREADY_RUNNING: CompactNotice = { messageKey: 'chat.compact.skip.busy', severity: 'info' };
const TEMPORARILY_PAUSED: CompactNotice = { messageKey: 'chat.compact.skip.paused', severity: 'warning' };
export const COMPACT_FAILED_NOTICE: CompactNotice = { messageKey: 'chat.compact.failed', severity: 'error' };

/**
 * The server's reasons that are not failures. A reason is a bare code, so it is matched whole.
 * A new chat gets its id in the browser, before its first message is sent, so the server has no chat of that id yet:
 * `chat_not_found` is a chat with nothing in it.
 */
const NOTICE_BY_REASON = new Map<string, CompactNotice>([
  ['no_compactable_messages', NOTHING_TO_COMPACT],
  ['too_few_messages', NOTHING_TO_COMPACT],
  ['chat_not_found', NOTHING_TO_COMPACT],
  ['concurrent_compaction_in_progress', ALREADY_RUNNING],
  ['summarize_circuit_open', TEMPORARILY_PAUSED],
]);

/**
 * What to tell the user about a request the server answered with `compacted: false`. Only reasons known to be benign
 * are worded as such: an unknown one (a timeout, a failed save, a code added later) must not read as "nothing to do".
 */
export function describeCompactSkip(reason: string | null): CompactNotice {
  return NOTICE_BY_REASON.get(reason ?? '') ?? COMPACT_FAILED_NOTICE;
}

/**
 * Compacts a chat on the user's request and hands a compaction that freed tokens to the store, so the summary banner,
 * the strategy panel and the usage ring catch up the same way whichever entry point asked. Messages are not rewritten
 * by a compaction, so none is reloaded.
 */
export async function runManualCompaction(chatId: string, focusTopic?: string): Promise<CompactResult> {
  const result = await compactChat(chatId, focusTopic);
  if (!result.compacted) {
    if (describeCompactSkip(result.reason).severity === 'error') {
      // The user reads a generic line; the server's reason is what a bug report needs.
      console.error('[Compaction] failed:', result.reason);
    }
    return result;
  }
  try {
    const { default: useChatStore } = await import('@/store/useChatStore');
    await useChatStore.getState().refreshCompactionState(chatId, {
      tokensSaved: result.tokens_saved,
      snapshotPath: result.backup_path ?? undefined,
    });
  } catch (error) {
    // The compaction happened; a store that could not take it must not turn it into a failure.
    console.error('[Compaction] store sync failed:', error);
  }
  return result;
}
