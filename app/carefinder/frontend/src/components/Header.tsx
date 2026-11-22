import { EmergencyButton } from "./EmergencyButton";
import { SpeakerIcon, SpeakerOffIcon } from "./icons";

export type Lang = "hi" | "en";

interface Props {
  lang: Lang;
  onToggleLang: () => void;
  demoMode: boolean;
  // Spoken replies; hidden when the browser can't speak.
  voice: { supported: boolean; muted: boolean; onToggle: () => void };
}

const NAV = [
  { label: "Find care", active: true },
  { label: "District gaps", active: false },
  { label: "Data quality", active: false },
];

export function Header({ lang, onToggleLang, demoMode, voice }: Props) {
  return (
    <header className="header">
      <span className="brand">Care Finder</span>

      <nav className="nav" aria-label="Dashboard sections">
        {NAV.map((item) =>
          item.active ? (
            <a key={item.label} className="nav-pill active" aria-current="page" href="/">
              {item.label}
            </a>
          ) : (
            <span
              key={item.label}
              className="nav-pill disabled"
              aria-disabled="true"
              title="Coming soon"
            >
              {item.label}
            </span>
          ),
        )}
      </nav>

      <span className="spacer" />

      {demoMode && <span className="badge-sample">Sample data</span>}

      <EmergencyButton />

      {voice.supported && (
        <button
          type="button"
          className="btn-outline icon-toggle"
          onClick={voice.onToggle}
          aria-pressed={!voice.muted}
          aria-label="Read replies aloud"
          title={voice.muted ? "Spoken replies off" : "Spoken replies on"}
        >
          {voice.muted ? <SpeakerOffIcon /> : <SpeakerIcon />}
        </button>
      )}

      <button
        type="button"
        className="btn-outline lang-toggle"
        onClick={onToggleLang}
        aria-label={lang === "hi" ? "Switch to English" : "हिंदी में बदलें"}
      >
        <span className={lang === "hi" ? "lang-on" : undefined}>हिं</span>
        {" / "}
        <span className={lang === "en" ? "lang-on" : undefined}>EN</span>
      </button>
    </header>
  );
}
