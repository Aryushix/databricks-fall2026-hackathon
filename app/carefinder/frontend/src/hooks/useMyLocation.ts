import { useCallback, useState } from "react";
import { loadMaps, placeNameFor } from "../maps";

export interface MyLocation {
  label: string;
  point: google.maps.LatLngLiteral;
}

type Status = "idle" | "locating" | "naming" | "error";

// Asks for the device's position only when the user taps the button
// (never on page load), then names the place with Google reverse
// geocoding so the search, which works on place names, can use it.
export function useMyLocation(mapsKey: string) {
  const supported =
    typeof navigator !== "undefined" && "geolocation" in navigator && window.isSecureContext;
  const [status, setStatus] = useState<Status>("idle");
  const [message, setMessage] = useState<string | null>(null);

  const locate = useCallback(async (): Promise<MyLocation | null> => {
    setMessage(null);
    setStatus("locating");

    let point: google.maps.LatLngLiteral;
    try {
      const pos = await new Promise<GeolocationPosition>((resolve, reject) =>
        navigator.geolocation.getCurrentPosition(resolve, reject, {
          enableHighAccuracy: false,
          timeout: 10000,
          maximumAge: 5 * 60 * 1000,
        }),
      );
      point = { lat: pos.coords.latitude, lng: pos.coords.longitude };
    } catch (err) {
      const denied = (err as GeolocationPositionError)?.code === 1;
      setStatus("error");
      setMessage(
        denied
          ? "Location blocked. Allow it in the browser, or type the place."
          : "Couldn't get your location. Please type the place.",
      );
      return null;
    }

    setStatus("naming");
    const label = mapsKey ? await loadMaps(mapsKey).then((libs) => placeNameFor(libs, point)).catch(() => null) : null;

    if (!label) {
      setStatus("error");
      setMessage("Found your position but couldn't name the place. Please type the town or PIN code.");
      return null;
    }

    setStatus("idle");
    return { label, point };
  }, [mapsKey]);

  // Clear the note once it no longer applies (e.g. Near was edited).
  const dismiss = useCallback(() => {
    setMessage(null);
    setStatus((s) => (s === "error" ? "idle" : s));
  }, []);

  return { supported, status, message, locate, dismiss };
}
