import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { useVoiceAgentBridge } from '../useVoiceAgentBridge';
import { ENDPOINT } from '../utteranceUplink';

vi.mock('@/lib/api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@/lib/api')>();
  return { ...actual, getWsUrl: (path: string) => `ws://127.0.0.1:8080${path}` };
});

vi.mock('@/lib/backend-health', () => ({
  isLocalBackendReadyCached: () => true,
  ensureLocalBackendReady: vi.fn(),
}));

const END_UTTERANCE = JSON.stringify({ type: 'end_utterance' });
const SPEECH = 0.2;

class MockWebSocket {
  static OPEN = 1;
  static instances: MockWebSocket[] = [];
  readyState = MockWebSocket.OPEN;
  binaryType = 'blob';
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onerror: (() => void) | null = null;
  onclose: (() => void) | null = null;
  send = vi.fn();
  close = vi.fn();

  constructor(readonly url: string) {
    MockWebSocket.instances.push(this);
  }
}

class MockRecorder {
  static instances: MockRecorder[] = [];
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

  constructor() {
    MockRecorder.instances.push(this);
  }

  /** What the browser does after `stop()`: the last chunk, then the stop event. */
  settle(lastChunk: Blob) {
    this.ondataavailable?.({ data: lastChunk });
    this.onstop?.();
  }
}

// The microphone level the analyser reports (a flat waveform at this amplitude has this RMS).
let micLevel = 0;

class MockAudioContext {
  createMediaStreamSource() {
    return { connect: vi.fn() };
  }

  createAnalyser() {
    return {
      fftSize: 0,
      getByteTimeDomainData: (data: Uint8Array) => data.fill(128 + Math.round(micLevel * 128)),
    };
  }

  close = vi.fn(async () => undefined);
}

const blob = () => new Blob([new Uint8Array(8)]);
const sockets = () => MockWebSocket.instances;
const sentFrames = (ws: MockWebSocket) => ws.send.mock.calls.map(([data]) => data as Blob | string);

describe('useVoiceAgentBridge microphone uplink', () => {
  async function startSession() {
    const hook = renderHook(() => useVoiceAgentBridge({ enabled: true }));
    await act(async () => {
      hook.result.current.connect();
    });
    await act(async () => {
      sockets().at(-1)?.onopen?.();
      await vi.advanceTimersByTimeAsync(0);
    });
    return hook;
  }

  async function speak(ms: number) {
    micLevel = SPEECH;
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
    micLevel = 0;
  }

  async function stayQuiet(ms: number) {
    await act(async () => {
      await vi.advanceTimersByTimeAsync(ms);
    });
  }

  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(performance, 'now').mockImplementation(() => Date.now());
    MockWebSocket.instances = [];
    MockRecorder.instances = [];
    micLevel = 0;
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
    vi.stubGlobal('MediaRecorder', MockRecorder as unknown as typeof MediaRecorder);
    vi.stubGlobal('AudioContext', MockAudioContext as unknown as typeof AudioContext);
    Object.defineProperty(globalThis.navigator, 'mediaDevices', {
      configurable: true,
      value: {
        getUserMedia: vi.fn(async () => ({ getTracks: () => [{ stop: vi.fn() }] }) as unknown as MediaStream),
      },
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  it('sends each utterance followed by end_utterance once the server announces batch STT', async () => {
    await startSession();
    const ws = sockets()[0];
    const first = MockRecorder.instances[0];

    await act(async () => {
      ws.onmessage?.({
        data: JSON.stringify({ type: 'info', message: 'streaming_unavailable', fallback: 'batch' }),
      });
    });
    const speech = blob();
    first.ondataavailable?.({ data: speech });
    await speak(1000);
    await stayQuiet(ENDPOINT.silenceMs + 200);

    // The recording is cut at the pause; its chunks leave only once the browser has delivered the last one.
    expect(first.stop).toHaveBeenCalledTimes(1);
    expect(sentFrames(ws).filter((frame) => typeof frame !== 'string')).toEqual([]);
    const tail = blob();
    act(() => first.settle(tail));

    const frames = sentFrames(ws);
    expect(frames[frames.length - 1]).toBe(END_UTTERANCE);
    expect(frames[frames.length - 2]).toBe(tail);
    expect(frames[frames.length - 3]).toBe(speech);
    expect(MockRecorder.instances).toHaveLength(2);
  });

  it('streams one continuous recording and never cuts it when the server sent no batch announcement', async () => {
    await startSession();
    const ws = sockets()[0];
    const chunk = blob();

    MockRecorder.instances[0].ondataavailable?.({ data: chunk });
    await speak(1000);
    await stayQuiet(ENDPOINT.silenceMs + ENDPOINT.idleRollMs);

    expect(sentFrames(ws).filter((frame) => frame === chunk)).toHaveLength(1);
    expect(sentFrames(ws)).not.toContain(END_UTTERANCE);
    expect(MockRecorder.instances).toHaveLength(1);
    expect(MockRecorder.instances[0].stop).not.toHaveBeenCalled();
  });

  it('forgets the batch announcement of a previous connection', async () => {
    const { result } = await startSession();
    await act(async () => {
      sockets()[0].onmessage?.({ data: JSON.stringify({ type: 'info', fallback: 'batch' }) });
    });

    await act(async () => {
      result.current.connect();
    });
    await act(async () => {
      sockets()[1].onopen?.();
      await vi.advanceTimersByTimeAsync(0);
    });
    const chunk = blob();
    MockRecorder.instances[MockRecorder.instances.length - 1].ondataavailable?.({ data: chunk });

    // Until the new server says otherwise the recording is streamed, not held back for an end_utterance.
    expect(sentFrames(sockets()[1])).toContain(chunk);
  });

  it('stops the recorder and sends nothing more when the session is closed', async () => {
    const { result } = await startSession();
    const ws = sockets()[0];
    const recorder = MockRecorder.instances[0];

    await act(async () => {
      ws.onmessage?.({ data: JSON.stringify({ type: 'info', fallback: 'batch' }) });
    });
    await speak(1000);
    act(() => result.current.disconnect());
    await stayQuiet(ENDPOINT.silenceMs + 200);

    expect(recorder.stop).toHaveBeenCalledTimes(1);
    expect(MockRecorder.instances).toHaveLength(1);
    expect(sentFrames(ws)).not.toContain(END_UTTERANCE);
  });
});
