import { useEffect, useRef } from "react";
import { MAX_SECONDS, useRecorder } from "../hooks/useRecorder";

interface Props {
  onRecorded: (wav: Blob) => void;
  // Called as recording starts (e.g. to stop a reply being read aloud,
  // so the microphone doesn't pick it up).
  onStart?: () => void;
  // True while the recording is being understood.
  busy: boolean;
}

const HOLD_MS = 450;

// Hold to speak (release to send) or tap to start / tap again to send.
// Keyboard: Enter or Space toggles; Escape cancels.
export function MicButton({ onRecorded, onStart, busy }: Props) {
  const rec = useRecorder();
  const press = useRef<{ at: number; startedHere: boolean } | null>(null);
  const ignoreClick = useRef(false);

  const finish = () => {
    const wav = rec.stop();
    if (wav) onRecorded(wav);
  };

  // Stop automatically at the time limit.
  useEffect(() => {
    if (rec.state === "recording" && rec.elapsed >= MAX_SECONDS) finish();
  }, [rec.elapsed, rec.state]);

  // Escape discards the recording.
  useEffect(() => {
    if (rec.state !== "recording") return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && rec.cancel();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [rec.state, rec.cancel]);

  const recording = rec.state === "recording";
  const secs = Math.floor(rec.elapsed);

  let status = "";
  if (busy) status = "समझ रहे हैं… · Understanding…";
  else if (recording) status = `सुन रहे हैं ${secs}s · Listening — tap again to send, Esc to cancel`;
  else if (rec.state === "starting") status = "Starting microphone…";
  else if (rec.error === "blocked") status = "Microphone blocked. Allow it in the browser, or type instead.";
  else if (rec.error === "unavailable") status = "No microphone found. Please type instead.";
  else if (rec.error === "too_short") status = "Too short. Hold the button while you speak.";

  return (
    <div className="mic">
      <button
        type="button"
        className={recording ? "btn-mic recording" : "btn-mic"}
        style={recording ? { boxShadow: `0 0 0 ${4 + rec.level * 10}px rgba(11,107,83,.25)` } : undefined}
        disabled={busy || rec.state === "starting"}
        aria-pressed={recording}
        aria-label={recording ? "Stop and send recording" : "Hold to speak, or tap to start speaking"}
        onPointerDown={(e) => {
          if (e.button !== 0) return;
          const startedHere = rec.state === "idle";
          press.current = { at: performance.now(), startedHere };
          if (startedHere) {
            onStart?.();
            void rec.start();
          }
        }}
        onPointerUp={() => {
          const p = press.current;
          press.current = null;
          if (!p) return;
          ignoreClick.current = true;
          const held = performance.now() - p.at;
          if (p.startedHere && held >= HOLD_MS) finish(); // hold-to-speak released
          else if (!p.startedHere) finish(); // second tap sends
          // A quick tap that started recording keeps it going.
        }}
        onClick={() => {
          // Pointer presses are handled above; this is keyboard activation.
          if (ignoreClick.current) {
            ignoreClick.current = false;
            return;
          }
          if (recording) finish();
          else {
            onStart?.();
            void rec.start();
          }
        }}
      >
        <MicIcon />
      </button>
      {status && (
        <span className={rec.error ? "mic-status mic-error" : "mic-status"} role="status">
          {status}
        </span>
      )}
    </div>
  );
}

function MicIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" aria-hidden="true">
      <path
        fill="currentColor"
        d="M12 14a3 3 0 0 0 3-3V5a3 3 0 0 0-6 0v6a3 3 0 0 0 3 3zm5-3a5 5 0 0 1-10 0H5a7 7 0 0 0 6 6.92V21h2v-3.08A7 7 0 0 0 19 11z"
      />
    </svg>
  );
}
