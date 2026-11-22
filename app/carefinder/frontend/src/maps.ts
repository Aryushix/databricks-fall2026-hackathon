import { importLibrary, setOptions } from "@googlemaps/js-api-loader";

// Loads the Google Maps JavaScript API once per page.
//
// All Maps calls run in the browser (map tiles, Geocoding, Routes), so
// the Databricks App's outbound-network limits don't apply to them.

export interface MapsLibs {
  Map: typeof google.maps.Map;
  LatLngBounds: typeof google.maps.LatLngBounds;
  Polyline: typeof google.maps.Polyline;
  AdvancedMarkerElement: typeof google.maps.marker.AdvancedMarkerElement;
  Geocoder: typeof google.maps.Geocoder;
  Route: typeof google.maps.routes.Route;
}

declare global {
  interface Window {
    // Called by the Maps API when the key is invalid or not allowed
    // for this site (e.g. missing referrer). Not in @types/google.maps.
    gm_authFailure?: () => void;
  }
}

let libsPromise: Promise<MapsLibs> | null = null;
const authFailureListeners = new Set<() => void>();
let authFailed = false;

export function onMapsAuthFailure(listener: () => void): () => void {
  if (authFailed) listener();
  authFailureListeners.add(listener);
  return () => authFailureListeners.delete(listener);
}

export function loadMaps(apiKey: string): Promise<MapsLibs> {
  if (libsPromise) return libsPromise;

  window.gm_authFailure = () => {
    authFailed = true;
    authFailureListeners.forEach((l) => l());
  };

  setOptions({ key: apiKey, v: "weekly", region: "IN", language: "en" });

  libsPromise = Promise.all([
    importLibrary("maps"),
    importLibrary("core"),
    importLibrary("marker"),
    importLibrary("geocoding"),
    importLibrary("routes"),
  ]).then(([maps, core, marker, geocoding, routes]) => ({
    Map: maps.Map,
    LatLngBounds: core.LatLngBounds,
    Polyline: maps.Polyline,
    AdvancedMarkerElement: marker.AdvancedMarkerElement,
    Geocoder: geocoding.Geocoder,
    Route: routes.Route,
  }));

  // Allow a retry after a network failure.
  libsPromise.catch(() => {
    libsPromise = null;
  });

  return libsPromise;
}

// ---------- Geocoding the "Near" text ----------

const geocodeCache = new Map<string, google.maps.LatLngLiteral | null>();

export async function geocodePlace(
  libs: MapsLibs,
  place: string,
): Promise<google.maps.LatLngLiteral | null> {
  const key = place.trim().toLowerCase();
  if (geocodeCache.has(key)) return geocodeCache.get(key)!;

  try {
    const { results } = await new libs.Geocoder().geocode({
      address: place,
      region: "in",
      componentRestrictions: { country: "IN" },
    });
    const loc = results[0]?.geometry.location;
    const point = loc ? { lat: loc.lat(), lng: loc.lng() } : null;
    geocodeCache.set(key, point);
    return point;
  } catch {
    // ZERO_RESULTS or Geocoding API not enabled: no patient point.
    return null;
  }
}

// ---------- Straight-line fallback ----------

export function haversineKm(a: google.maps.LatLngLiteral, b: google.maps.LatLngLiteral): number {
  const R = 6371;
  const rad = (d: number) => (d * Math.PI) / 180;
  const dLat = rad(b.lat - a.lat);
  const dLng = rad(b.lng - a.lng);
  const h =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(h));
}

export function formatMinutes(ms: number): string {
  const mins = Math.round(ms / 60000);
  if (mins < 60) return `${mins} min`;
  const h = Math.floor(mins / 60);
  const m = mins % 60;
  return m ? `${h} h ${m} min` : `${h} h`;
}

// ---------- Reverse geocoding ("Use my location") ----------

// A place name the search understands: town or district, plus the PIN
// code when known ("Gaya, 823001"). Null if Google can't name it (or the
// Geocoding API isn't allowed for the key).
export async function placeNameFor(
  libs: MapsLibs,
  point: google.maps.LatLngLiteral,
): Promise<string | null> {
  try {
    const { results } = await new libs.Geocoder().geocode({ location: point, language: "en" });
    const components = results.flatMap((r) => r.address_components);
    const find = (type: string) => components.find((c) => c.types.includes(type))?.long_name;
    const town =
      find("locality") ??
      find("administrative_area_level_3") ??
      find("administrative_area_level_2");
    if (!town) return null;
    const pin = find("postal_code");
    return pin ? `${town}, ${pin}` : town;
  } catch {
    return null;
  }
}
