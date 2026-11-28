/**
 * [INPUT]
 * - MediaStream (POS: microphone stream, owned by the caller)
 * - WebSocket (POS: `/ws/voice/session` socket, owned by the caller)
 *
 * [OUTPUT]
 * - ENDPOINT: speech detection and utterance length thresholds
 * - UtteranceEndpointer: level-driven end-of-speech detector
 * - UtteranceUplink / createUtteranceUplink: microphone → socket uplink that cuts the recording into utterances for batch STT
 *
 * [POS]
 * Audio uplink of the voice session socket. A streaming STT provider (Deepgram) transcribes one continuous
 * recording as it arrives. Every other provider transcribes whole utterances when the server receives
 * `end_utterance`, and a recording can only be decoded from its first chunk on, so each utterance is its own
 * recording: the uplink stops it after the speaker pauses, sends its chunks plus `end_utterance`, and starts the next.
 * Recordings without speech are not uploaded; their bare `end_utterance` is all the server hears of a quiet session.
 */

export const ENDPOINT = {
  /** Level (RMS, 0..1) from which a sample counts as speech; ≈ -34 dBFS, above what noise suppression leaves. */
  voiceRms: 0.02,
  /** Voiced time a recording needs before it is worth transcribing; shorter blips (clicks, coughs) are dropped. */
  minSpeechMs: 200,
  /** Silence after speech that ends the utterance. */
  silenceMs: 1200,
  /** A recording that never held speech is dropped after this long, so an idle session sends nothing. */
  idleRollMs: 10_000,
  /** Longest single utterance; a room that never goes quiet is still transcribed in bounded pieces. */
  maxUtteranceMs: 60_000,
} as const;

const RECORDER_TIMESLICE_MS = 100;
const END_UTTERANCE = JSON.stringify({ type: 'end_utterance' });

/** Decides from a stream of level samples when the current recording should be cut. */
export class UtteranceEndpointer {
  private speechMs = 0;
  private silenceMs = 0;
  private ageMs = 0;
  private lastSampleAt: number | null = null;

  /** True once the recording holds enough voiced time to be transcribed. */
  get spoke(): boolean {
    return this.speechMs >= ENDPOINT.minSpeechMs;
  }

  /** Starts measuring a fresh recording. */
  reset(): void {
    this.speechMs = 0;
    this.silenceMs = 0;
    this.ageMs = 0;
    this.lastSampleAt = null;
  }

  /** Feeds the level measured at `nowMs`; true when the current recording should be cut. */
  push(rms: number, nowMs: number): boolean {
    // Measured from timestamps rather than a nominal period: hidden tabs throttle timers to about 1 Hz.
    const elapsedMs = this.lastSampleAt === null ? 0 : Math.max(0, nowMs - this.lastSampleAt);
    this.lastSampleAt = nowMs;
    this.ageMs += elapsedMs;
    if (rms >= ENDPOINT.voiceRms) {
      this.speechMs += elapsedMs;
      this.silenceMs = 0;
    } else {
      this.silenceMs += elapsedMs;
    }
    if (this.ageMs >= ENDPOINT.maxUtteranceMs) {
      return true;
    }
    if (this.spoke) {
      return this.silenceMs >= ENDPOINT.silenceMs;
    }
    // Rolled only in silence: a cut while the first syllables of an utterance are being spoken would clip them.
    return this.ageMs >= ENDPOINT.idleRollMs && this.silenceMs >= ENDPOINT.silenceMs;
  }
}

export interface UtteranceUplink {
  /** Feeds one microphone level sample (RMS, 0..1). */
  onLevel: (rms: number) => void;
  /** Stops recording; an utterance still in progress is discarded. */
  stop: () => void;
}

interface UplinkOptions {
  ws: WebSocket;
  stream: MediaStream;
  /** True once the server announced batch STT. Until then the recording is streamed as one continuous piece. */
  isBatchStt: () => boolean;
  /** Clock for level samples, in ms. */
  now?: () => number;
}

interface Recording {
  recorder: MediaRecorder;
  /** Chunks held back until the utterance ends (batch STT only). */
  held: Blob[];
  /** Whether any chunk of this recording already reached the server. */
  sent: boolean;
}

export function createUtteranceUplink({
  ws,
  stream,
  isBatchStt,
  now = () => performance.now(),
}: UplinkOptions): UtteranceUplink {
  const mimeType = MediaRecorder.isTypeSupported('audio/webm;codecs=opus') ? 'audio/webm;codecs=opus' : 'audio/webm';
  const endpointer = new UtteranceEndpointer();
  let recording: Recording | null = null;
  let cutting = false;
  let stopped = false;

  const send = (data: Blob | string) => {
    if (ws.readyState === WebSocket.OPEN) {
      ws.send(data);
    }
  };

  const startRecording = () => {
    endpointer.reset();
    cutting = false;
    const current: Recording = {
      recorder: new MediaRecorder(stream, { mimeType }),
      held: [],
      sent: false,
    };
    recording = current;

    current.recorder.ondataavailable = (event) => {
      if (stopped || event.data.size === 0) {
        return;
      }
      if (isBatchStt()) {
        current.held.push(event.data);
      } else {
        current.sent = true;
        send(event.data);
      }
    };

    current.recorder.onstop = () => {
      // A recorder that stopped by itself (microphone unplugged) must not be restarted in a loop.
      if (stopped || !cutting) {
        return;
      }
      // Silence is not uploaded: the server would pay to transcribe it. The bare end_utterance that replaces it is
      // skipped by the server but still a frame, so the session's idle timeout never takes a quiet user for gone.
      if (current.sent || endpointer.spoke) {
        current.held.forEach((chunk) => send(chunk));
      }
      send(END_UTTERANCE);
      startRecording();
    };

    current.recorder.start(RECORDER_TIMESLICE_MS);
  };

  startRecording();

  return {
    onLevel: (rms) => {
      // A recorder that is not recording is already being cut, or has died: there is nothing left to cut.
      if (stopped || !recording || !isBatchStt() || recording.recorder.state !== 'recording') {
        return;
      }
      if (endpointer.push(rms, now())) {
        // `stop` delivers the last chunk and then `onstop`, which sends the utterance and begins the next one.
        cutting = true;
        recording.recorder.stop();
      }
    },
    stop: () => {
      stopped = true;
      if (recording?.recorder.state === 'recording') {
        recording.recorder.stop();
      }
    },
  };
}
