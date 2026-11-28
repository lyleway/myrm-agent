import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createUtteranceUplink, ENDPOINT, UtteranceEndpointer } from '../utteranceUplink';

const SPEECH = 0.2;
const QUIET = 0;
const SAMPLE_MS = 50;
const END_UTTERANCE = JSON.stringify({ type: 'end_utterance' });

describe('UtteranceEndpointer', () => {
  /** Feeds `ms` of constant level in 50 ms samples; returns the time (within this call) of the first cut, if any. */
  function feed(endpointer: UtteranceEndpointer, clock: { now: number }, rms: number, ms: number): number | null {
    for (let elapsed = 0; elapsed < ms; elapsed += SAMPLE_MS) {
      clock.now += SAMPLE_MS;
      if (endpointer.push(rms, clock.now)) {
        return elapsed + SAMPLE_MS;
      }
    }
    return null;
  }

  it('cuts only after the speaker has been silent for the configured time', () => {
    const endpointer = new UtteranceEndpointer();
    const clock = { now: 0 };

    expect(feed(endpointer, clock, SPEECH, 2000)).toBeNull();
    expect(endpointer.spoke).toBe(true);

    const cutAfter = feed(endpointer, clock, QUIET, 5000);
    expect(cutAfter).not.toBeNull();
    expect(cutAfter).toBeGreaterThanOrEqual(ENDPOINT.silenceMs);
    expect(cutAfter).toBeLessThan(ENDPOINT.silenceMs + 2 * SAMPLE_MS);
  });

  it('does not cut on a pause shorter than the silence window', () => {
    const endpointer = new UtteranceEndpointer();
    const clock = { now: 0 };

    feed(endpointer, clock, SPEECH, 1000);
    expect(feed(endpointer, clock, QUIET, ENDPOINT.silenceMs - 2 * SAMPLE_MS)).toBeNull();
    // Speech resumes: the silence counter starts over, so the next pause is measured from here.
    expect(feed(endpointer, clock, SPEECH, 500)).toBeNull();
    expect(feed(endpointer, clock, QUIET, ENDPOINT.silenceMs - 2 * SAMPLE_MS)).toBeNull();
  });

  it('rolls a recording that never held speech and reports it as silent', () => {
    const endpointer = new UtteranceEndpointer();
    const clock = { now: 0 };

    expect(feed(endpointer, clock, QUIET, ENDPOINT.idleRollMs - 2 * SAMPLE_MS)).toBeNull();
    expect(feed(endpointer, clock, QUIET, 4 * SAMPLE_MS)).not.toBeNull();
    expect(endpointer.spoke).toBe(false);
  });

  it('does not roll an idle recording while the first syllables of an utterance are being spoken', () => {
    const endpointer = new UtteranceEndpointer();
    const clock = { now: 0 };

    feed(endpointer, clock, QUIET, ENDPOINT.idleRollMs - 2 * SAMPLE_MS);
    // The recording turns ten seconds old in the middle of the speaker's first word.
    expect(feed(endpointer, clock, SPEECH, 3 * SAMPLE_MS)).toBeNull();
    expect(endpointer.spoke).toBe(false);

    expect(feed(endpointer, clock, SPEECH, 1000)).toBeNull();
    expect(endpointer.spoke).toBe(true);
    expect(feed(endpointer, clock, QUIET, ENDPOINT.silenceMs + SAMPLE_MS)).not.toBeNull();
  });

  it('does not treat a short blip as speech', () => {
    const endpointer = new UtteranceEndpointer();
    const clock = { now: 0 };

    feed(endpointer, clock, QUIET, 1000);
    feed(endpointer, clock, SPEECH, ENDPOINT.minSpeechMs - 2 * SAMPLE_MS);

    expect(endpointer.spoke).toBe(false);
    // Silence after the blip must not end an "utterance" that never started.
    expect(feed(endpointer, clock, QUIET, ENDPOINT.silenceMs + 1000)).toBeNull();
  });

  it('cuts continuous speech at the maximum utterance length', () => {
    const endpointer = new UtteranceEndpointer();
    const clock = { now: 0 };

    expect(feed(endpointer, clock, SPEECH, ENDPOINT.maxUtteranceMs - SAMPLE_MS)).toBeNull();
    expect(feed(endpointer, clock, SPEECH, 2 * SAMPLE_MS)).not.toBeNull();
  });

  it('measures time from timestamps so throttled timers still end an utterance', () => {
    const endpointer = new UtteranceEndpointer();

    // A hidden tab delivers about one sample per second.
    expect(endpointer.push(SPEECH, 0)).toBe(false);
    expect(endpointer.push(SPEECH, 1000)).toBe(false);
    expect(endpointer.spoke).toBe(true);
    expect(endpointer.push(QUIET, 2000)).toBe(false);
    expect(endpointer.push(QUIET, 3000)).toBe(true);
  });

  it('starts counting from zero after a reset', () => {
    const endpointer = new UtteranceEndpointer();
    const clock = { now: 0 };

    feed(endpointer, clock, SPEECH, 1000);
    endpointer.reset();

    expect(endpointer.spoke).toBe(false);
  });
});

class FakeRecorder {
  static instances: FakeRecorder[] = [];
  static isTypeSupported = () => true;

  state: 'inactive' | 'recording' = 'inactive';
  ondataavailable: ((event: { data: Blob }) => void) | null = null;
  onstop: (() => void) | null = null;
  start = vi.fn(() => {
    this.state = 'recording';
  });
  stop = vi.fn(() => {
    this.state = 'inactive';
  });

  readonly options: MediaRecorderOptions;

  constructor(_stream: MediaStream, options: MediaRecorderOptions) {
    this.options = options;
    FakeRecorder.instances.push(this);
  }

  chunk(data: Blob) {
    this.ondataavailable?.({ data });
  }

  /** What the browser does some time after `stop()`: the last chunk, then the stop event. */
  settle(lastChunk?: Blob) {
    if (lastChunk) {
      this.chunk(lastChunk);
    }
    this.onstop?.();
  }
}

describe('createUtteranceUplink', () => {
  const stream = {} as MediaStream;
  let socket: { readyState: number; send: ReturnType<typeof vi.fn> };
  let clock: { now: number };
  let batch: boolean;

  const blob = () => new Blob([new Uint8Array(8)]);
  const sent = () => socket.send.mock.calls.map(([data]) => data as Blob | string);
  const lastRecorder = () => FakeRecorder.instances[FakeRecorder.instances.length - 1];

  function open() {
    return createUtteranceUplink({
      ws: socket as unknown as WebSocket,
      stream,
      isBatchStt: () => batch,
      now: () => clock.now,
    });
  }

  function feed(uplink: ReturnType<typeof open>, rms: number, ms: number) {
    for (let elapsed = 0; elapsed < ms; elapsed += SAMPLE_MS) {
      clock.now += SAMPLE_MS;
      uplink.onLevel(rms);
    }
  }

  beforeEach(() => {
    FakeRecorder.instances = [];
    socket = { readyState: WebSocket.OPEN, send: vi.fn() };
    clock = { now: 0 };
    batch = true;
    vi.stubGlobal('MediaRecorder', FakeRecorder as unknown as typeof MediaRecorder);
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('streams every chunk as it arrives and never cuts while the server expects one continuous recording', () => {
    batch = false;
    const uplink = open();
    const [first, second] = [blob(), blob()];

    lastRecorder().chunk(first);
    lastRecorder().chunk(second);
    feed(uplink, SPEECH, 1000);
    feed(uplink, QUIET, ENDPOINT.silenceMs + 1000);

    expect(sent()).toHaveLength(2);
    expect(sent()[0]).toBe(first);
    expect(sent()[1]).toBe(second);
    expect(FakeRecorder.instances).toHaveLength(1);
    expect(lastRecorder().stop).not.toHaveBeenCalled();
  });

  it('holds an utterance until the speaker pauses, then sends it whole followed by end_utterance', () => {
    const uplink = open();
    const [header, speech, tail] = [blob(), blob(), blob()];
    const first = lastRecorder();

    first.chunk(header);
    feed(uplink, SPEECH, 1000);
    first.chunk(speech);
    expect(sent()).toEqual([]);

    feed(uplink, QUIET, ENDPOINT.silenceMs + SAMPLE_MS);
    expect(first.stop).toHaveBeenCalledTimes(1);
    // Nothing leaves before the browser delivered the recording's last chunk.
    expect(sent()).toEqual([]);

    first.settle(tail);
    expect(sent()).toHaveLength(4);
    expect(sent()[0]).toBe(header);
    expect(sent()[1]).toBe(speech);
    expect(sent()[2]).toBe(tail);
    expect(sent()[3]).toBe(END_UTTERANCE);
  });

  it('starts the next utterance in a fresh recording so that it carries its own container header', () => {
    const uplink = open();
    const first = lastRecorder();

    feed(uplink, SPEECH, 1000);
    feed(uplink, QUIET, ENDPOINT.silenceMs + SAMPLE_MS);
    first.settle(blob());

    expect(FakeRecorder.instances).toHaveLength(2);
    const second = lastRecorder();
    expect(second).not.toBe(first);
    expect(second.start).toHaveBeenCalledWith(100);
    expect(second.options.mimeType).toBe('audio/webm;codecs=opus');

    // The new recording is judged on its own: a second utterance is cut and sent just like the first.
    const nextSpeech = blob();
    second.chunk(nextSpeech);
    feed(uplink, SPEECH, 1000);
    feed(uplink, QUIET, ENDPOINT.silenceMs + SAMPLE_MS);
    second.settle();

    const frames = sent();
    expect(frames.filter((frame) => frame === END_UTTERANCE)).toHaveLength(2);
    expect(frames[frames.length - 2]).toBe(nextSpeech);
  });

  it('uploads nothing of a recording that never held speech but still ends it, so the session is seen alive', () => {
    const uplink = open();
    const first = lastRecorder();

    first.chunk(blob());
    feed(uplink, QUIET, ENDPOINT.idleRollMs + 2 * SAMPLE_MS);
    expect(first.stop).toHaveBeenCalledTimes(1);
    first.settle(blob());

    expect(sent()).toEqual([END_UTTERANCE]);
    expect(FakeRecorder.instances).toHaveLength(2);
    expect(lastRecorder().start).toHaveBeenCalled();
  });

  it('keeps a quiet session alive with one bare end_utterance per idle recording', () => {
    const uplink = open();

    for (let round = 0; round < 3; round += 1) {
      feed(uplink, QUIET, ENDPOINT.idleRollMs + 2 * SAMPLE_MS);
      lastRecorder().settle();
    }

    expect(sent()).toEqual([END_UTTERANCE, END_UTTERANCE, END_UTTERANCE]);
    expect(FakeRecorder.instances).toHaveLength(4);
  });

  it('keeps chunks that were already streamed before the server announced batch STT', () => {
    batch = false;
    const uplink = open();
    const [early, late] = [blob(), blob()];
    const first = lastRecorder();

    first.chunk(early);
    batch = true;
    first.chunk(late);
    feed(uplink, QUIET, ENDPOINT.idleRollMs + 2 * SAMPLE_MS);
    first.settle();

    // The server already holds `early`; without end_utterance it would glue the next recording onto it.
    expect(sent()).toHaveLength(3);
    expect(sent()[0]).toBe(early);
    expect(sent()[1]).toBe(late);
    expect(sent()[2]).toBe(END_UTTERANCE);
  });

  it('cuts only once while the browser is still finishing the recording', () => {
    const uplink = open();
    const first = lastRecorder();

    feed(uplink, SPEECH, 1000);
    feed(uplink, QUIET, ENDPOINT.silenceMs + 5000);

    expect(first.stop).toHaveBeenCalledTimes(1);
  });

  it('does not restart a recorder that stopped by itself', () => {
    const uplink = open();
    const first = lastRecorder();

    feed(uplink, SPEECH, 1000);
    first.chunk(blob());
    first.state = 'inactive';
    first.settle();

    expect(sent()).toEqual([]);
    expect(FakeRecorder.instances).toHaveLength(1);
    // A stopped recorder gives the endpointer nothing to cut.
    feed(uplink, QUIET, ENDPOINT.silenceMs + SAMPLE_MS);
    expect(first.stop).not.toHaveBeenCalled();
  });

  it('discards the utterance in progress when stopped', () => {
    const uplink = open();
    const first = lastRecorder();

    feed(uplink, SPEECH, 1000);
    first.chunk(blob());
    uplink.stop();
    first.settle(blob());

    expect(first.stop).toHaveBeenCalledTimes(1);
    expect(sent()).toEqual([]);
    expect(FakeRecorder.instances).toHaveLength(1);
    feed(uplink, QUIET, ENDPOINT.silenceMs + SAMPLE_MS);
    expect(first.stop).toHaveBeenCalledTimes(1);
  });

  it('starts no new recording when the session stops while an utterance is being cut', () => {
    const uplink = open();
    const first = lastRecorder();

    feed(uplink, SPEECH, 1000);
    feed(uplink, QUIET, ENDPOINT.silenceMs + SAMPLE_MS);
    expect(first.stop).toHaveBeenCalledTimes(1);
    uplink.stop();
    first.settle(blob());

    expect(FakeRecorder.instances).toHaveLength(1);
    expect(sent()).toEqual([]);
  });

  it('sends nothing that the recorder still delivers after the session was stopped', () => {
    batch = false;
    const uplink = open();
    const first = lastRecorder();

    first.chunk(blob());
    uplink.stop();
    first.settle(blob());

    expect(sent()).toHaveLength(1);
  });

  it('writes nothing to a socket that is no longer open', () => {
    batch = false;
    socket.readyState = WebSocket.CLOSED;
    open();

    lastRecorder().chunk(blob());

    expect(socket.send).not.toHaveBeenCalled();
  });
});
