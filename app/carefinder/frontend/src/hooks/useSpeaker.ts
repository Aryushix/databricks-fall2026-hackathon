import { useCallback, useEffect, useState } from "react";
import type { Spoken } from "../types";

// Reads replies aloud with the browser's built-in voices
// (window.speechSynthesis). Gemini voice output isn't available on the
// current key's free tier; if it becomes available, synthesise on the
// server and play the audio here instead, keeping this as the fallback.

const MUTE_KEY = "care-finder-muted";

const LOCALES: Record<string, string> = { hi: "hi-IN", en: "en-IN" };

function readMuted(): boolean {
  try {
    return localStorage.getItem(MUTE_KEY) === "1";
  } catch {
    return false;
  }
}

function findVoice(voices: SpeechSynthesisVoice[], lang: string) {
  const locale = LOCALES[lang] ?? lang;
  return (
    voices.find((v) => v.lang.replace("_", "-") === locale) ??
    voices.find((v) => v.lang.toLowerCase().startsWith(lang))
  );
}

export function useSpeaker() {
  const supported = typeof window !== "undefined" && "speechSynthesis" in window;
  const [muted, setMuted] = useState(readMuted);
  const [speaking, setSpeaking] = useState(false);
  const [voices, setVoices] = useState<SpeechSynthesisVoice[]>([]);

  // Voices load asynchronously in Chrome.
  useEffect(() => {
    if (!supported) return;
    const load = () => setVoices(window.speechSynthesis.getVoices());
    load();
    window.speechSynthesis.addEventListener("voiceschanged", load);
    return () => window.speechSynthesis.removeEventListener("voiceschanged", load);
  }, [supported]);

  const cancel = useCallback(() => {
    if (!supported) return;
    window.speechSynthesis.cancel();
    setSpeaking(false);
  }, [supported]);

  // Speak the reply in its language; if the browser has no voice for
  // it (e.g. no Hindi voice), use English instead of mispronouncing.
  const speak = useCallback(
    (spoken: Spoken, opts: { force?: boolean } = {}) => {
      if (!supported || (muted && !opts.force)) return;
      let lang = spoken.lang;
      let voice = findVoice(voices, lang);
      if (!voice && lang !== "en") {
        lang = "en";
        voice = findVoice(voices, "en");
      }
      const text = spoken[lang as "hi" | "en"] ?? spoken.en;

      window.speechSynthesis.cancel();
      const utterance = new SpeechSynthesisUtterance(text);
      utterance.lang = LOCALES[lang] ?? lang;
      if (voice) utterance.voice = voice;
      utterance.rate = 0.95;
      utterance.onstart = () => setSpeaking(true);
      utterance.onend = () => setSpeaking(false);
      utterance.onerror = () => setSpeaking(false);
      window.speechSynthesis.speak(utterance);
    },
    [supported, muted, voices],
  );

  const toggleMuted = useCallback(() => {
    setMuted((m) => {
      const next = !m;
      try {
        localStorage.setItem(MUTE_KEY, next ? "1" : "0");
      } catch {
        // Storage blocked: the setting lasts for this page only.
      }
      if (next) window.speechSynthesis?.cancel();
      return next;
    });
  }, []);

  return { supported, muted, speaking, speak, cancel, toggleMuted };
}
