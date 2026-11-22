import { useEffect, useRef, useState } from "react";
import { EmergencyBanner } from "./components/EmergencyBanner";
import { Header, type Lang } from "./components/Header";
import { HeardBar } from "./components/HeardBar";
import { ListenButton } from "./components/ListenButton";
import { MapPanel } from "./components/MapPanel";
import { MicButton } from "./components/MicButton";
import { ResultsList } from "./components/ResultsList";
import { SearchBar, type FocusRequest, type SearchValues } from "./components/SearchBar";
import { StatusPanel } from "./components/StatusPanel";
import { WhyPanel } from "./components/WhyPanel";
import { useOrchestrator } from "./hooks/useOrchestrator";
import { useSpeaker } from "./hooks/useSpeaker";
import { useMyLocation, type MyLocation } from "./hooks/useMyLocation";
import { LocateIcon } from "./components/icons";
import { SERVICES } from "./labels";
import type { ParsedSpeech } from "./types";

export default function App() {
  const [lang, setLang] = useState<Lang>("hi");
  const [demoMode, setDemoMode] = useState(true);
  const [maps, setMaps] = useState({ key: "", mapId: "DEMO_MAP_ID" });
  const [demoSearches, setDemoSearches] = useState<{ service: string; location: string }[]>([]);
  const [search, setSearch] = useState<SearchValues>({
    service: "",
    location: "",
    withinMinutes: 120,
  });
  const [focus, setFocus] = useState<FocusRequest | null>(null);
  const [selectedId, setSelectedId] = useState<string | null>(null);
  // Last thing the speech layer understood, and whether it sounded
  // like an emergency.
  const [heard, setHeard] = useState<ParsedSpeech | null>(null);
  const [emergency, setEmergency] = useState(false);
  // Snapshot the pending prompt when microphone recording begins.
  const recordingConfirmationId = useRef<string | null>(null);

  const { latest, results, busy, networkError, retryable, send, sendVoice, retry } =
    useOrchestrator();
  const speaker = useSpeaker();
  const myLocation = useMyLocation(maps.key);
  // Set by "Use my location"; its exact point is used on the map while
  // the Near field still holds the name it filled in.
  const [here, setHere] = useState<MyLocation | null>(null);

  useEffect(() => {
    fetch("/api/config")
      .then((r) => (r.ok ? r.json() : null))
      .then((cfg) => {
        if (!cfg) return;
        setDemoMode(Boolean(cfg.demo_mode));
        setMaps({ key: cfg.maps_browser_key ?? "", mapId: cfg.maps_map_id || "DEMO_MAP_ID" });
        setDemoSearches(cfg.demo_searches ?? []);
      })
      .catch(() => {});
  }, []);

  // Select #1 whenever a new set of results arrives.
  useEffect(() => {
    setSelectedId(results?.facilities[0]?.facility_id ?? null);
  }, [results]);

  // Point the user at the field the orchestrator is asking about.
  useEffect(() => {
    // "No, change" returns a clarification with no missing fields;
    // the Near field is the most likely thing to fix.
    if (!latest) return;
    const missing = latest.missing_fields;
    if (missing.includes("service") || latest.status === "reset") {
      setFocus((f) => ({ field: "service", n: (f?.n ?? 0) + 1 }));
    } else if (latest.status === "clarification_required") {
      setFocus((f) => ({ field: "location", n: (f?.n ?? 0) + 1 }));
    }
  }, [latest]);

  // When the button the user pressed disappears (e.g. "Yes, search"),
  // focus falls to <body>. Move it to the new prompt or the results.
  useEffect(() => {
    requestAnimationFrame(() => {
      if (document.activeElement !== document.body) return;
      const target =
        document.querySelector<HTMLElement>(".status") ??
        document.getElementById("results-heading");
      target?.focus();
    });
  }, [latest]);

  const onRecorded = async (wav: Blob) => {
    const result = await sendVoice(wav, lang, {
      service: search.service,
      location: search.location,
    }, recordingConfirmationId.current);
    if (!result) return;
    const { parsed, response } = result;
    // Someone who spoke probably wants to hear the answer.
    if (response.spoken) speaker.speak(response.spoken);
    setHeard(parsed);
    if (parsed.is_emergency) setEmergency(true);
    if (parsed.location) myLocation.dismiss();
    // Mirror what was understood in the form so it can be corrected.
    setSearch((s) => ({
      ...s,
      service: SERVICES.some((x) => x.value === parsed.service) ? parsed.service! : s.service,
      location: parsed.location ?? s.location,
    }));
  };

  const doSearch = () => {
    setHeard(null);
    return send({
      intent: "find_hospital",
      service: search.service,
      location: search.location,
      preferences: { within_minutes: search.withinMinutes },
      language: lang,
    });
  };

  const facilities = results?.facilities ?? [];
  const excluded = results?.excluded_facilities ?? [];
  const selectedIndex = facilities.findIndex((h) => h.facility_id === selectedId);
  const selected = selectedIndex >= 0 ? facilities[selectedIndex] : null;
  const stale = latest !== results && latest?.status !== "error";

  return (
    <div className="page">
      <Header
        lang={lang}
        onToggleLang={() => setLang(lang === "hi" ? "en" : "hi")}
        demoMode={demoMode}
        voice={{
          supported: speaker.supported,
          muted: speaker.muted,
          onToggle: speaker.toggleMuted,
        }}
      />

      {emergency && <EmergencyBanner onDismiss={() => setEmergency(false)} />}

      <SearchBar
        values={search}
        onChange={(next) => {
          if (next.location !== search.location) myLocation.dismiss();
          setSearch(next);
        }}
        onSearch={doSearch}
        onReset={() => {
          setHeard(null);
          setEmergency(false);
          setSearch((s) => ({ ...s, location: "" }));
          send({ intent: "reset_search" });
        }}
        focus={focus}
        mic={
          <MicButton
            onRecorded={onRecorded}
            onStart={() => {
              speaker.cancel();
              recordingConfirmationId.current =
                latest?.confirmation?.confirmation_id ??
                latest?.context?.pending_confirmation?.confirmation_id ??
                null;
            }}
            busy={busy}
          />
        }
        nearAction={
          myLocation.supported && (
            <button
              type="button"
              className="btn-outline btn-locate"
              aria-label="Use my location"
              title="Use my location"
              disabled={busy || myLocation.status === "locating" || myLocation.status === "naming"}
              onClick={async () => {
                const found = await myLocation.locate();
                if (!found) return;
                setHere(found);
                setSearch((s) => ({ ...s, location: found.label }));
              }}
            >
              <LocateIcon />
            </button>
          )
        }
        nearNote={
          myLocation.status === "locating"
            ? "Finding your location…"
            : myLocation.status === "naming"
              ? "Looking up the place name…"
              : myLocation.message
        }
        busy={busy}
      />

      {heard && <HeardBar heard={heard} />}

      <StatusPanel
        response={latest}
        networkError={networkError}
        retryable={retryable}
        busy={busy}
        onConfirm={(yes) => send({
          intent: "confirm_search",
          confirmation: yes,
          confirmation_id:
            latest?.confirmation?.confirmation_id ??
            latest?.context?.pending_confirmation?.confirmation_id,
        })}
        onRetry={retry}
        onAskForHelp={() => send({ intent: "request_assistance" })}
        demoSearches={demoSearches}
        onTryDemo={(d) => {
          setHeard(null);
          setSearch((s) => ({ ...s, service: d.service, location: d.location }));
          myLocation.dismiss();
          send({
            intent: "find_hospital",
            service: d.service,
            location: d.location,
            preferences: { within_minutes: search.withinMinutes },
            language: lang,
          });
        }}
      />

      {speaker.supported && latest?.spoken && !networkError && (
        <ListenButton
          speaking={speaker.speaking}
          onListen={() => speaker.speak(latest.spoken!, { force: true })}
          onStop={speaker.cancel}
        />
      )}

      {demoMode && results?.status === "results" && (
        <p className="demo-banner" role="status">
          {results.message}
        </p>
      )}

      {facilities.length > 0 ? (
        <main className="body">
          <ResultsList
            facilities={facilities}
            excluded={excluded}
            stale={stale}
            selectedId={selectedId}
            onSelect={setSelectedId}
          />
          <MapPanel
            facilities={facilities}
            selectedId={selectedId}
            onSelect={setSelectedId}
            place={results?.context?.confirmed_location ?? null}
            patientPoint={
              here && results?.context?.confirmed_location === here.label ? here.point : null
            }
            mapsKey={maps.key}
            mapId={maps.mapId}
          />
          {selected && (
            <WhyPanel
              facility={selected}
              rank={selectedIndex + 1}
              next={facilities[selectedIndex + 1] ?? null}
              uncertainties={results?.answer_card?.uncertainties ?? []}
            />
          )}
        </main>
      ) : excluded.length > 0 ? (
        <main className="body">
          <ResultsList
            facilities={[]}
            excluded={excluded}
            stale={stale}
            selectedId={null}
            onSelect={() => {}}
          />
        </main>
      ) : (
        !results && (
          <main className="body empty">
            <p>
              Choose a service and a place, then press <strong>Search</strong>. We'll confirm
              what we understood before searching.
            </p>
            <p className="emergency-line">
              Emergency? <a href="tel:108">Call 108</a> first.
            </p>
          </main>
        )
      )}
    </div>
  );
}
