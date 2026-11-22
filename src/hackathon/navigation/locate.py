"""Turn a place the user types into a point to measure distance from.

"823001", "Gaya", "Aurangabad, Bihar" -> latitude/longitude, through the India Post directory and
`location_check.Directory`. A name that exists in more than one state is never guessed: the caller gets the
states to choose from. Nothing typed here is stored or logged; the point is used for this one request.

Directory: the CSV at NAV_PIN_DIRECTORY if that is set, else the copy bundled with the `indiapins` package.
"""

from __future__ import annotations

import bisect
import difflib
import os
import re
from functools import lru_cache
from typing import Optional

import numpy as np
import pandas as pd

from .. import cleaning
from .. import location_check as lc
from .contract import LocationCandidate, LocationResolution

PIN_RE = re.compile(r"(?<!\d)([1-9]\d{2})\s?(\d{3})(?!\d)")
MAX_CANDIDATES = 8
# A state's extent is 1.5 x the distance from its middle that 95% of its post offices are within, plus 50 km
# (99% for territories with under 200 offices, whose enclaves are far apart). On the 2026 directory the
# largest is about 1,170 km (Gujarat) and Bihar's is 380 km.
EXTENT_FACTOR, EXTENT_MARGIN_KM, SMALL_STATE = 1.5, 50.0, 200


def _centre(points: np.ndarray) -> tuple[float, float, float]:
    """(lat, lon, spread_km): the median point and the median distance of the points from it."""
    lat, lon = (float(v) for v in np.median(points, axis=0))
    spread = float(np.median(cleaning.haversine_km(lat, lon, points[:, 0], points[:, 1])))
    return round(lat, 5), round(lon, 5), round(spread, 1)


def _not_found(note: str) -> LocationResolution:
    return LocationResolution(status="not_found", note=note)


class LocationResolver:
    def __init__(self, directory: lc.Directory):
        self.d = directory
        self.district_keys: dict[str, set] = {}             # state -> place keys that are district names
        for pin, keys in directory.pin_dkeys.items():
            for st in directory.pin_states.get(pin, []):
                self.district_keys.setdefault(st, set()).update(keys)
        self.district_states: dict[str, list] = {}           # district key -> states that have it
        for st, keys in self.district_keys.items():
            for k in keys:
                self.district_states.setdefault(k, []).append(st)
        self.district_names = sorted(self.district_states)
        # each state's extent: the median of its offices and a radius that holds nearly all of them
        self.extent: dict[str, tuple[float, float, float]] = {}
        states = np.array([s if isinstance(s, str) else "" for s in directory.office_state])
        for st in set(states) - {""}:
            p = directory.office_pts[states == st]
            c = np.median(p, axis=0)
            dist = cleaning.haversine_km(c[0], c[1], p[:, 0], p[:, 1])
            q = np.percentile(dist, 99 if len(p) < SMALL_STATE else 95)
            self.extent[str(st)] = (float(c[0]), float(c[1]), float(q * EXTENT_FACTOR + EXTENT_MARGIN_KM))

    def _in_right_state(self, lat: float, lon: float, states) -> bool:
        """The directory has whole groups of offices filed under one state with another state's coordinates
        (all of Aurangabad, Bihar sits on Aurangabad, Maharashtra). A point is kept only if it lies within
        the extent of one of the expected states."""
        known = [self.extent[s] for s in states if s in self.extent]
        if not known:
            return True
        return any(cleaning.haversine_km(clat, clon, lat, lon) <= radius for clat, clon, radius in known)

    @classmethod
    def from_raw(cls, raw: pd.DataFrame) -> "LocationResolver":
        """Any copy of the India Post directory (see location_check.DIRECTORY_COLUMNS)."""
        return cls(lc.Directory(lc.normalize_directory(raw)))

    def resolve(self, text: str, state: Optional[str] = None) -> LocationResolution:
        text = (text or "").strip()
        if not text:
            return _not_found("Type a city, town, district or 6-digit PIN code.")
        m = PIN_RE.search(text)
        if m:
            return self._pin(m.group(1) + m.group(2))
        place, st = text, None
        if "," in text:                                     # "Aurangabad, Bihar"
            head, tail = text.rsplit(",", 1)
            st = cleaning.resolve_state(tail.strip())
            if st:
                place = head
        if state and state.strip():
            st = cleaning.resolve_state(state.strip()) or st
        return self._place(place.strip(), st)

    # ---- PIN ------------------------------------------------------------- #
    def _pin(self, pin: str) -> LocationResolution:
        if pin not in self.d.pin_states:
            return _not_found(f"PIN {pin} isn't in the India Post directory. Check it, or type the town name.")
        pts = self.d.pin_points.get(pin)
        where = ", ".join([*self.d.pin_districts.get(pin, [])[:2], *self.d.pin_states.get(pin, [])[:1]])
        label = f"PIN {pin}" + (f" ({where})" if where else "")
        if pts is None or not len(pts):
            return _not_found(f"{label} has no map position in the directory. Type the town name instead.")
        lat, lon, spread = _centre(pts)
        if not self._in_right_state(lat, lon, self.d.pin_states.get(pin, [])):
            return _not_found(f"The directory's map position for {label} is wrong (it points to another state). "
                              "Type the name of a nearby town instead.")
        return LocationResolution(status="found", latitude=lat, longitude=lon, label=label, method="pin",
                                  spread_km=spread)

    # ---- place name ------------------------------------------------------ #
    def _keys(self, name: str) -> list[str]:
        name = cleaning.CITY_ALIASES.get(name.lower(), name)       # "Bangalore" -> "Bengaluru"
        k = lc.place_key(name)
        return [k, *lc._ALIASES.get(k, [])] if k else []

    def _found(self, name: str, state: str, pts: np.ndarray, method: str, note=None) -> LocationResolution:
        lat, lon, spread = _centre(pts)
        if not self._in_right_state(lat, lon, [state]):
            return _not_found(f"The directory's map position for {name}, {state} is wrong (it points to another "
                              "state). Type a nearby town or a 6-digit PIN code instead.")
        return LocationResolution(status="found", latitude=lat, longitude=lon, label=f"{name}, {state}",
                                  method=method, spread_km=spread, note=note)

    def _prefix(self, keys: list[str], state: str) -> Optional[tuple[str, np.ndarray]]:
        """'Rohini' -> 'rohini sector 3': names that start with the typed one (fast, no fuzzy matching)."""
        names, places = self.d.names[state], self.d.places[state]
        for k in keys:
            if len(k) < 4 or k in lc.GENERIC_TOKENS:
                continue
            lo, hits, matched = bisect.bisect_left(names, k + " "), [], None
            while lo < len(names) and names[lo].startswith(k + " "):
                hits.append(places[names[lo]])
                matched = matched or names[lo]
                lo += 1
            if hits:
                return matched, np.vstack(hits)
        return None

    def _district_spelling(self, keys: list[str], shown: str) -> Optional[LocationResolution]:
        """'Ahmedabad' -> the district India Post spells 'Ahmadabad', when only one state has it."""
        for k in keys:
            if len(k) < 5:
                continue
            close = [c for c in difflib.get_close_matches(k, self.district_names, n=3, cutoff=lc.FUZZY_CUTOFF)
                     if c[0] == k[0] and len(self.district_states[c]) == 1]
            if len(close) == 1:
                st = self.district_states[close[0]][0]
                pts = self.d.places.get(st, {}).get(close[0])
                if pts is not None and len(pts):
                    return self._found(close[0].title(), st, pts, "place_approx",
                                       note=f"Using the {close[0].title()} district in {st} for '{shown}'.")
        return None

    def _place(self, name: str, state: Optional[str]) -> LocationResolution:
        keys = self._keys(name)
        shown = name.strip().title()
        if not keys:
            return _not_found("Type a city, town, district or 6-digit PIN code.")
        if state:
            if state not in self.d.places:
                return _not_found(f"There are no {state} places in the India Post directory.")
            hit = self.d._in_state(keys, state)              # exact, alias, prefix, close spelling
            if hit is None or not len(hit[1]):
                return _not_found(f"Couldn't find '{shown}' in {state}. Check the spelling, or use a PIN code.")
            how = hit[0]
            if how == "exact" or how.startswith("alias:"):
                return self._found(shown, state, hit[1], "place")
            if how.startswith("prefix:"):
                return self._found(shown, state, hit[1], "place_approx",
                                   note=f"Using the places in {state} whose names start with '{shown}'.")
            matched = how.split(":", 1)[1].title()
            return self._found(matched, state, hit[1], "place_approx",
                               note=f"No place is called '{shown}' in {state}; using {matched}.")
        exact = [(s, np.vstack([self.d.places[s][k] for k in keys if k in self.d.places[s]]))
                 for s in sorted(self.d.places) if any(k in self.d.places[s] for k in keys)]
        exact = [(s, p) for s, p in exact if len(p)]
        if len(exact) == 1:
            return self._found(shown, exact[0][0], exact[0][1], "place")
        if len(exact) > 1:                                  # "Patna": a district only in Bihar
            districts = [(s, p) for s, p in exact if any(k in self.district_keys.get(s, ()) for k in keys)]
            if len(districts) == 1:
                others = ", ".join(s for s, _ in exact if s != districts[0][0])
                return self._found(shown, districts[0][0], districts[0][1], "place",
                                   note=f"Using the district in {districts[0][0]}. "
                                        f"There is also a place called {shown} in: {others}.")
            if not districts and (hit := self._district_spelling(keys, shown)):
                return hit
            exact = districts or exact
            exact.sort(key=lambda sp: -len(sp[1]))
            return LocationResolution(
                status="ambiguous",
                candidates=[LocationCandidate(place=shown, state=s) for s, _ in exact[:MAX_CANDIDATES]],
                note=f"There is a '{shown}' in {len(exact)} states. Choose the state.")
        if hit := self._district_spelling(keys, shown):
            return hit
        loose = [(s, h) for s in sorted(self.d.places) if (h := self._prefix(keys, s)) and len(h[1])]
        if len(loose) == 1:
            s, (matched, pts) = loose[0]
            return self._found(matched.title(), s, pts, "place_approx",
                               note=f"No place is called exactly '{shown}'; using {matched.title()}, {s}.")
        if len(loose) > 1:
            return LocationResolution(
                status="ambiguous",
                candidates=[LocationCandidate(place=m.title(), state=s) for s, (m, _) in loose[:MAX_CANDIDATES]],
                note=f"No place is called exactly '{shown}'. Did you mean one of these?")
        return _not_found(f"Couldn't find '{shown}'. Add the state (e.g. 'Gaya, Bihar'), check the spelling, "
                          "or use a 6-digit PIN code.")


@lru_cache(maxsize=1)
def default_resolver() -> LocationResolver:
    path = os.getenv("NAV_PIN_DIRECTORY")
    raw = pd.read_csv(path, dtype=str, low_memory=False) if path else lc.load_indiapins()
    return LocationResolver.from_raw(raw)
