import { useEffect, useRef, useState } from "react";
import { formatMinutes, geocodePlace, haversineKm, loadMaps, onMapsAuthFailure, type MapsLibs } from "../maps";
import type { Hospital } from "../types";

interface Props {
  facilities: Hospital[];
  selectedId: string | null;
  onSelect: (id: string) => void;
  // The searched place ("Gaya"), geocoded as the patient's position.
  place: string | null;
  // Exact position from "Use my location"; used instead of geocoding.
  patientPoint?: google.maps.LatLngLiteral | null;
  apiKey: string;
  mapId: string;
  // Called when the key is rejected or the API can't load.
  onFail: () => void;
}

const INDIA_CENTER = { lat: 22.5, lng: 79 };
const ROUTE_COLOR = "#0B6B53";

type Overlay = { setMap(map: google.maps.Map | null): void };

export function GoogleMap({
  facilities,
  selectedId,
  onSelect,
  place,
  patientPoint,
  apiKey,
  mapId,
  onFail,
}: Props) {
  const divRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<google.maps.Map | null>(null);
  const libsRef = useRef<MapsLibs | null>(null);
  const markersRef = useRef(new Map<string, { marker: google.maps.marker.AdvancedMarkerElement; el: HTMLElement }>());
  const extrasRef = useRef<{ markers: google.maps.marker.AdvancedMarkerElement[]; overlays: Overlay[] }>({
    markers: [],
    overlays: [],
  });
  const onSelectRef = useRef(onSelect);
  onSelectRef.current = onSelect;
  const onFailRef = useRef(onFail);
  onFailRef.current = onFail;

  const [ready, setReady] = useState(false);
  const [chip, setChip] = useState("Loading map…");

  // 1. Load the API and create the map once.
  useEffect(() => {
    let cancelled = false;
    const unsubscribe = onMapsAuthFailure(() => onFailRef.current());

    loadMaps(apiKey)
      .then((libs) => {
        if (cancelled || !divRef.current) return;
        libsRef.current = libs;
        mapRef.current = new libs.Map(divRef.current, {
          mapId,
          center: INDIA_CENTER,
          zoom: 5,
          mapTypeControl: false,
          streetViewControl: false,
          fullscreenControl: false,
          clickableIcons: false,
        });
        setReady(true);
      })
      .catch(() => !cancelled && onFailRef.current());

    return () => {
      cancelled = true;
      unsubscribe();
    };
  }, [apiKey, mapId]);

  // 2. Draw facilities, the patient point and the route to #1.
  useEffect(() => {
    const map = mapRef.current;
    const libs = libsRef.current;
    if (!ready || !map || !libs) return;
    let cancelled = false;

    clearAll(markersRef.current, extrasRef.current);

    const bounds = new libs.LatLngBounds();
    facilities.forEach((h, i) => {
      if (h.latitude == null || h.longitude == null) return;
      const position = { lat: h.latitude, lng: h.longitude };
      const el = document.createElement("div");
      el.className = pinClass(h, i + 1);
      el.textContent = String(i + 1);
      const marker = new libs.AdvancedMarkerElement({
        map,
        position,
        content: el,
        title: `#${i + 1} ${h.name}`,
        gmpClickable: true,
        zIndex: facilities.length - i,
      });
      marker.addEventListener("gmp-click", () => onSelectRef.current(h.facility_id));
      markersRef.current.set(h.facility_id, { marker, el });
      bounds.extend(position);
    });

    const fit = () => {
      if (bounds.isEmpty()) return;
      const ne = bounds.getNorthEast();
      const sw = bounds.getSouthWest();
      if (ne.equals(sw)) {
        map.setCenter(ne);
        map.setZoom(13);
      } else {
        // Extra bottom padding keeps pins clear of the caption chip.
        map.fitBounds(bounds, { top: 50, left: 50, right: 50, bottom: 100 });
      }
    };
    fit();

    const first = facilities[0];
    const firstPos =
      first?.latitude != null && first.longitude != null
        ? { lat: first.latitude, lng: first.longitude }
        : null;

    (async () => {
      const patient = patientPoint ?? (place ? await geocodePlace(libs, place) : null);
      if (cancelled) return;

      if (!patient) {
        setChip(place ? `Couldn't place "${place}" on the map · showing ranked places only` : "Ranked places");
        return;
      }

      const el = document.createElement("div");
      el.className = "gpin-patient";
      el.setAttribute("aria-hidden", "true");
      extrasRef.current.markers.push(
        new libs.AdvancedMarkerElement({ map, position: patient, content: el, title: `Patient: ${place}` }),
      );
      bounds.extend(patient);
      fit();

      if (!firstPos) {
        setChip(`Patient: ${place}`);
        return;
      }

      try {
        const { routes } = await libs.Route.computeRoutes({
          origin: patient,
          destination: firstPos,
          travelMode: "DRIVING",
          region: "in",
          fields: ["path", "durationMillis", "distanceMeters"],
        });
        if (cancelled) return;
        const route = routes?.[0];
        if (!route) throw new Error("no route");
        route
          .createPolylines({ polylineOptions: { strokeColor: ROUTE_COLOR, strokeWeight: 5, strokeOpacity: 0.9 } })
          .forEach((line) => {
            line.setMap(map);
            extrasRef.current.overlays.push(line);
          });
        const km = route.distanceMeters != null ? ` (${(route.distanceMeters / 1000).toFixed(1)} km)` : "";
        const time = route.durationMillis != null ? formatMinutes(route.durationMillis) : "time unknown";
        setChip(`Patient: ${place} · route to #1, ${time}${km} by road`);
      } catch {
        if (cancelled) return;
        // Routes API unavailable or no road route: dashed straight line,
        // labelled so it isn't mistaken for a road route.
        const line = new libs.Polyline({
          map,
          path: [patient, firstPos],
          strokeOpacity: 0,
          icons: [
            {
              icon: { path: "M 0,-1 0,1", strokeOpacity: 1, strokeColor: ROUTE_COLOR, scale: 3 },
              offset: "0",
              repeat: "14px",
            },
          ],
        });
        extrasRef.current.overlays.push(line);
        const km = haversineKm(patient, firstPos).toFixed(1);
        setChip(`Patient: ${place} · #1 is ~${km} km in a straight line (road route unavailable)`);
      }
    })();

    return () => {
      cancelled = true;
    };
  }, [ready, facilities, place, patientPoint]);

  // 3. Highlight the selected facility.
  useEffect(() => {
    markersRef.current.forEach(({ el }, id) => {
      el.classList.toggle("pin-selected", id === selectedId);
    });
  }, [selectedId, ready, facilities]);

  // Remove everything when the map unmounts.
  useEffect(() => {
    const markers = markersRef.current;
    const extras = extrasRef.current;
    return () => clearAll(markers, extras);
  }, []);

  return (
    <>
      <div ref={divRef} className="gmap" role="region" aria-label="Map of ranked places" />
      <div className="map-chip" role="status">
        {chip}
      </div>
    </>
  );
}

function pinClass(h: Hospital, rank: number): string {
  return [
    "gpin",
    rank === 1 ? "pin-primary" : "",
    h.trust_label === "claimed_only" ? "pin-claimed" : "",
  ]
    .filter(Boolean)
    .join(" ");
}

function clearAll(
  markers: Map<string, { marker: google.maps.marker.AdvancedMarkerElement }>,
  extras: { markers: google.maps.marker.AdvancedMarkerElement[]; overlays: Overlay[] },
) {
  markers.forEach(({ marker }) => (marker.map = null));
  markers.clear();
  extras.markers.forEach((m) => (m.map = null));
  extras.overlays.forEach((o) => o.setMap(null));
  extras.markers = [];
  extras.overlays = [];
}
