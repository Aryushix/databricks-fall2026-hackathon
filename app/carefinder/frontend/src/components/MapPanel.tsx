import { useState } from "react";
import type { Hospital } from "../types";
import { GoogleMap } from "./GoogleMap";

interface Props {
  facilities: Hospital[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  place: string | null;
  patientPoint?: google.maps.LatLngLiteral | null;
  mapsKey: string;
  mapId: string;
}

// Google Maps when a browser key is configured; otherwise (or if the key
// is rejected) a placeholder that plots facilities by coordinates.
export function MapPanel({
  facilities,
  selectedId,
  onSelect,
  place,
  patientPoint,
  mapsKey,
  mapId,
}: Props) {
  const [failed, setFailed] = useState(false);

  return (
    <section className="map" aria-label="Map of ranked places">
      {mapsKey && !failed ? (
        <GoogleMap
          facilities={facilities}
          selectedId={selectedId}
          onSelect={onSelect}
          place={place}
          patientPoint={patientPoint}
          apiKey={mapsKey}
          mapId={mapId}
          onFail={() => setFailed(true)}
        />
      ) : (
        <PlaceholderMap
          facilities={facilities}
          selectedId={selectedId}
          onSelect={onSelect}
          note={
            failed
              ? "Google Maps couldn't load (check the key and its allowed sites) · preview only"
              : "Map preview · add MAPS_BROWSER_KEY for the real map and route"
          }
        />
      )}
    </section>
  );
}

function PlaceholderMap({
  facilities,
  selectedId,
  onSelect,
  note,
}: Omit<Props, "place" | "patientPoint" | "mapsKey" | "mapId"> & { note: string }) {
  const placed = facilities.filter((h) => h.latitude != null && h.longitude != null);

  const lats = placed.map((h) => h.latitude as number);
  const lngs = placed.map((h) => h.longitude as number);
  const pad = 0.02;
  const minLat = Math.min(...lats) - pad;
  const maxLat = Math.max(...lats) + pad;
  const minLng = Math.min(...lngs) - pad;
  const maxLng = Math.max(...lngs) + pad;

  return (
    <>
      <div className="map-placeholder">
        {placed.map((h) => {
          const rank = facilities.indexOf(h) + 1;
          // Keep pins inside 10–90% across and 8–78% down so the
          // caption chip at the bottom never covers them.
          const x = 10 + ((h.longitude! - minLng) / (maxLng - minLng)) * 80;
          const y = 8 + (1 - (h.latitude! - minLat) / (maxLat - minLat)) * 70;
          const cls = [
            "pin",
            rank === 1 ? "pin-primary" : "",
            h.trust_label === "claimed_only" ? "pin-claimed" : "",
            h.facility_id === selectedId ? "pin-selected" : "",
          ].join(" ");
          return (
            <button
              key={h.facility_id}
              type="button"
              className={cls}
              style={{ left: `${x}%`, top: `${y}%` }}
              onClick={() => onSelect(h.facility_id)}
              aria-label={`#${rank} ${h.name}`}
            >
              {rank}
            </button>
          );
        })}
      </div>
      <div className="map-chip">{placed.length ? note : "No coordinates to show"}</div>
    </>
  );
}
