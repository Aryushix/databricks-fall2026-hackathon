"""Travel time interface. Off by default: Free Edition blocks most outbound traffic, so no provider is
configured, and no response ever shows a travel time or calls the nearest facility the fastest."""

from __future__ import annotations

from typing import Optional, Protocol

from .contract import TravelEstimate


class TravelEstimator(Protocol):
    def estimate(self, origin_lat: float, origin_lon: float, dest_lat: float, dest_lon: float) -> Optional[TravelEstimate]: ...


class NullTravelEstimator:
    def estimate(self, origin_lat, origin_lon, dest_lat, dest_lon) -> Optional[TravelEstimate]:
        return None
