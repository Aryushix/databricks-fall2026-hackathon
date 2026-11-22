import { useCallback, useEffect, useRef, useState } from "react";

// Records the microphone as 16 kHz mono 16-bit WAV.
//
// MediaRecorder would give webm/opus in Chrome (mp4 in Safari), which
// Gemini doesn't accept, so we capture raw samples with an AudioWorklet
// and encode WAV ourselves.

const TARGET_RATE = 16000;
const MIN_SECONDS = 0.4;
export const MAX_SECONDS = 30;

const WORKLET_SOURCE = `
class CaptureProcessor extends AudioWorkletProcessor {
  process(inputs) {
    const channel = inputs[0] && inputs[0][0];
    if (channel) this.port.postMessage(channel.slice(0));
    return true;
  }
}
registerProcessor("capture-processor", CaptureProcessor);
`;

export type RecorderState = "idle" | "starting" | "recording";
export type RecorderError = "blocked" | "unavailable" | "too_short" | null;

interface Session {
  stream: MediaStream;
  context: AudioContext;
  chunks: Float32Array[];
  startedAt: number;
}

export function useRecorder() {
  const [state, setState] = useState<RecorderState>("idle");
  const [error, setError] = useState<RecorderError>(null);
  const [elapsed, setElapsed] = useState(0);
  const [level, setLevel] = useState(0);
  const session = useRef<Session | null>(null);
  const timer = useRef<number | null>(null);

  const teardown = useCallback(() => {
    if (timer.current) window.clearInterval(timer.current);
    timer.current = null;
    const s = session.current;
    session.current = null;
    if (s) {
      s.stream.getTracks().forEach((t) => t.stop());
      void s.context.close();
    }
    setState("idle");
    setLevel(0);
  }, []);

  useEffect(() => teardown, [teardown]);

  const start = useCallback(async () => {
    if (session.current) return;
    setError(null);
    setElapsed(0);
    setState("starting");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true, autoGainControl: true },
      });
      const context = new AudioContext();
      const url = URL.createObjectURL(new Blob([WORKLET_SOURCE], { type: "text/javascript" }));
      await context.audioWorklet.addModule(url);
      URL.revokeObjectURL(url);

      const source = context.createMediaStreamSource(stream);
      const capture = new AudioWorkletNode(context, "capture-processor");
      // A silent path to the output keeps the graph running.
      const mute = context.createGain();
      mute.gain.value = 0;
      source.connect(capture).connect(mute).connect(context.destination);

      const s: Session = { stream, context, chunks: [], startedAt: performance.now() };
      let lastLevelAt = 0;
      capture.port.onmessage = (e: MessageEvent<Float32Array>) => {
        s.chunks.push(e.data);
        const now = performance.now();
        if (now - lastLevelAt > 100) {
          lastLevelAt = now;
          let sum = 0;
          for (const v of e.data) sum += v * v;
          setLevel(Math.min(1, Math.sqrt(sum / e.data.length) * 6));
        }
      };
      session.current = s;
      timer.current = window.setInterval(
        () => setElapsed((performance.now() - s.startedAt) / 1000),
        200,
      );
      setState("recording");
    } catch (err) {
      teardown();
      const name = (err as DOMException)?.name;
      setError(name === "NotAllowedError" || name === "SecurityError" ? "blocked" : "unavailable");
    }
  }, [teardown]);

  // Stop and return the WAV, or null if nothing usable was recorded.
  const stop = useCallback((): Blob | null => {
    const s = session.current;
    if (!s) return null;
    const rate = s.context.sampleRate;
    const seconds = (performance.now() - s.startedAt) / 1000;
    const chunks = s.chunks;
    teardown();
    if (seconds < MIN_SECONDS) {
      setError("too_short");
      return null;
    }
    return encodeWav(downsample(concat(chunks), rate, TARGET_RATE), TARGET_RATE);
  }, [teardown]);

  const cancel = useCallback(() => teardown(), [teardown]);

  return { state, error, elapsed, level, start, stop, cancel };
}

function concat(chunks: Float32Array[]): Float32Array {
  const out = new Float32Array(chunks.reduce((n, c) => n + c.length, 0));
  let offset = 0;
  for (const c of chunks) {
    out.set(c, offset);
    offset += c.length;
  }
  return out;
}

// Averages each block of input samples into one output sample.
function downsample(input: Float32Array, inRate: number, outRate: number): Float32Array {
  if (inRate <= outRate) return input;
  const ratio = inRate / outRate;
  const out = new Float32Array(Math.floor(input.length / ratio));
  for (let i = 0; i < out.length; i++) {
    const startIdx = Math.floor(i * ratio);
    const end = Math.min(input.length, Math.floor((i + 1) * ratio));
    let sum = 0;
    for (let j = startIdx; j < end; j++) sum += input[j];
    out[i] = sum / Math.max(1, end - startIdx);
  }
  return out;
}

function encodeWav(samples: Float32Array, rate: number): Blob {
  const buffer = new ArrayBuffer(44 + samples.length * 2);
  const view = new DataView(buffer);
  const writeStr = (offset: number, s: string) => {
    for (let i = 0; i < s.length; i++) view.setUint8(offset + i, s.charCodeAt(i));
  };
  writeStr(0, "RIFF");
  view.setUint32(4, 36 + samples.length * 2, true);
  writeStr(8, "WAVE");
  writeStr(12, "fmt ");
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // PCM
  view.setUint16(22, 1, true); // mono
  view.setUint32(24, rate, true);
  view.setUint32(28, rate * 2, true); // byte rate
  view.setUint16(32, 2, true); // block align
  view.setUint16(34, 16, true); // bits per sample
  writeStr(36, "data");
  view.setUint32(40, samples.length * 2, true);
  for (let i = 0; i < samples.length; i++) {
    const v = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(44 + i * 2, v < 0 ? v * 0x8000 : v * 0x7fff, true);
  }
  return new Blob([buffer], { type: "audio/wav" });
}
