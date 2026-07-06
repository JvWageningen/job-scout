"""Travel time calculation using OSRM, OpenRouteService, and NS APIs."""

from __future__ import annotations

import math
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any

import requests
from loguru import logger

from job_scout.models import Config, TravelMode, TravelTime

if TYPE_CHECKING:
    from job_scout.database import Database

NOMINATIM_BASE = "https://nominatim.openstreetmap.org"
OSRM_BASE = "https://router.project-osrm.org/route/v1"
ORS_BASE = "https://api.openrouteservice.org/v2"
NS_BASE = "https://gateway.apiportal.ns.nl/reisinformatie-api/api/v3"

# Exact-match keywords: if the full location (lowercased/stripped) equals one of these,
# it is considered vague (no specific city).
_EXACT_VAGUE = frozenset(["remote", "netherlands", "nederland", "everywhere", "overal"])

# Substring keywords: if the location *contains* one of these, it is vague.
_SUBSTR_VAGUE = frozenset(["thuis", "thuiswerken"])


_EARTH_RADIUS_KM = 6371.0


def _haversine_km(
    coord1: tuple[float, float],
    coord2: tuple[float, float],
) -> float:
    """Calculate straight-line distance between two (lon, lat) points.

    Args:
        coord1: (longitude, latitude) of first point.
        coord2: (longitude, latitude) of second point.

    Returns:
        Distance in kilometres.
    """
    lon1, lat1 = math.radians(coord1[0]), math.radians(coord1[1])
    lon2, lat2 = math.radians(coord2[0]), math.radians(coord2[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    )
    return _EARTH_RADIUS_KM * 2 * math.asin(math.sqrt(a))


def _geocode(
    address: str,
    db: Database | None = None,
    cache_days: int = 90,
) -> tuple[float, float] | None:
    """Geocode an address using Nominatim OSM. Returns (lon, lat) or None.

    Args:
        address: Human-readable address string.
        db: Optional database for caching geocode results.
        cache_days: Cache validity period in days (default 90).

    Returns:
        (longitude, latitude) tuple, or None if geocoding fails.
    """
    # Check cache first
    if db:
        cached = db.get_cached_geocode(address, cache_days)
        if cached is not None:
            logger.debug(f"Geocode cache hit for '{address}'")
            return cached

    try:
        resp = requests.get(
            f"{NOMINATIM_BASE}/search",
            params={"q": address, "format": "json", "limit": "1"},
            headers={"User-Agent": "job-scout/0.1 (job search tool)"},
            timeout=10,
        )
        resp.raise_for_status()
        results: list[dict[str, str]] = resp.json()
        if results:
            lon = float(results[0]["lon"])
            lat = float(results[0]["lat"])
            # Save to cache
            if db:
                db.save_geocode_cache(address, lat, lon)
            return lon, lat
    except requests.RequestException as e:
        logger.warning(f"Geocoding failed for '{address}': {e}")
    return None


def _get_osrm_time(
    origin: tuple[float, float],
    destination: tuple[float, float],
    profile: str,
    db: Database | None = None,
    cache_days: int = 14,
) -> float | None:
    """Get travel time in minutes from the free OSRM public server.

    Args:
        origin: (lon, lat) of origin.
        destination: (lon, lat) of destination.
        profile: OSRM profile ('driving' or 'bike').
        db: Optional database for caching travel time results.
        cache_days: Cache validity period in days (default 14).

    Returns:
        Travel time in minutes, or None on failure.
    """
    # Build cache keys
    origin_key = f"{origin[0]},{origin[1]}"
    dest_key = f"{destination[0]},{destination[1]}"
    mode_key = f"osrm_{profile}"

    # Check cache first
    if db:
        cached = db.get_cached_travel_time(origin_key, dest_key, mode_key, cache_days)
        if cached is not None:
            logger.debug(f"Travel time cache hit (OSRM {profile})")
            return cached

    coords = f"{origin[0]},{origin[1]};{destination[0]},{destination[1]}"
    try:
        resp = requests.get(
            f"{OSRM_BASE}/{profile}/{coords}",
            params={"overview": "false"},
            headers={"User-Agent": "job-scout/0.1 (job search tool)"},
            timeout=10,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        if data.get("code") != "Ok":
            return None
        duration_s: float = data["routes"][0]["duration"]
        minutes = round(duration_s / 60, 1)
        # Save to cache
        if db:
            db.save_travel_time_cache(origin_key, dest_key, mode_key, minutes)
        return minutes
    except requests.RequestException as e:
        logger.warning(f"OSRM '{profile}' request failed: {e}")
    except (KeyError, IndexError) as e:
        logger.warning(f"OSRM '{profile}' response parse error: {e}")
    return None


def _get_ors_time(
    origin: tuple[float, float],
    destination: tuple[float, float],
    profile: str,
    api_key: str,
    db: Database | None = None,
    cache_days: int = 14,
) -> float | None:
    """Get travel time in minutes from OpenRouteService.

    Args:
        origin: (lon, lat) of origin.
        destination: (lon, lat) of destination.
        profile: ORS routing profile (e.g. 'driving-car').
        api_key: OpenRouteService API key.
        db: Optional database for caching travel time results.
        cache_days: Cache validity period in days (default 14).

    Returns:
        Travel time in minutes, or None on failure.
    """
    # Build cache keys
    origin_key = f"{origin[0]},{origin[1]}"
    dest_key = f"{destination[0]},{destination[1]}"
    mode_key = f"ors_{profile}"

    # Check cache first
    if db:
        cached = db.get_cached_travel_time(origin_key, dest_key, mode_key, cache_days)
        if cached is not None:
            logger.debug(f"Travel time cache hit (ORS {profile})")
            return cached

    try:
        resp = requests.post(
            f"{ORS_BASE}/directions/{profile}",
            json={"coordinates": [list(origin), list(destination)]},
            headers={"Authorization": api_key, "Content-Type": "application/json"},
            timeout=10,
        )
        resp.raise_for_status()
        data: dict[str, Any] = resp.json()
        duration_s: float = data["routes"][0]["summary"]["duration"]
        minutes = round(duration_s / 60, 1)
        # Save to cache
        if db:
            db.save_travel_time_cache(origin_key, dest_key, mode_key, minutes)
        return minutes
    except requests.RequestException as e:
        logger.warning(f"ORS '{profile}' request failed: {e}")
    except (KeyError, IndexError) as e:
        logger.warning(f"ORS '{profile}' response parse error: {e}")
    return None


def _get_ns_time(
    origin: tuple[float, float],
    destination: tuple[float, float],
    api_key: str,
    db: Database | None = None,
    cache_days: int = 14,
) -> float | None:
    """Get public transport travel time in minutes from NS Journey Planner.

    Args:
        origin: (lon, lat) of origin.
        destination: (lon, lat) of destination.
        api_key: NS API subscription key.
        db: Optional database for caching travel time results.
        cache_days: Cache validity period in days (default 14).

    Returns:
        Travel time in minutes, or None on failure.
    """
    # Build cache keys
    origin_key = f"{origin[0]},{origin[1]}"
    dest_key = f"{destination[0]},{destination[1]}"
    mode_key = "ns_public_transport"

    # Check cache first
    if db:
        cached = db.get_cached_travel_time(origin_key, dest_key, mode_key, cache_days)
        if cached is not None:
            logger.debug("Travel time cache hit (NS public transport)")
            return cached

    try:
        resp = requests.get(
            f"{NS_BASE}/trips",
            params={
                "originLat": origin[1],
                "originLng": origin[0],
                "destinationLat": destination[1],
                "destinationLng": destination[0],
            },
            headers={
                "Ocp-Apim-Subscription-Key": api_key,
                "Accept": "application/json",
            },
            timeout=15,
        )
        resp.raise_for_status()
        trips: list[dict[str, Any]] = resp.json().get("trips", [])
        if not trips:
            return None
        minutes = _parse_ns_duration(trips[0])
        # Save to cache if we got a result
        if minutes is not None and db:
            db.save_travel_time_cache(origin_key, dest_key, mode_key, minutes)
        return minutes
    except requests.RequestException as e:
        logger.warning(f"NS API request failed: {e}")
    except (KeyError, IndexError) as e:
        logger.warning(f"NS API response parse error: {e}")
    return None


def _parse_ns_duration(trip: dict[str, Any]) -> float | None:
    """Extract duration in minutes from an NS trip object.

    Args:
        trip: A single trip dict from the NS API response.

    Returns:
        Duration in minutes, or None if not determinable.
    """
    if "plannedDurationInMinutes" in trip:
        return float(trip["plannedDurationInMinutes"])
    legs: list[dict[str, Any]] = trip.get("legs", [])
    if not legs:
        return None
    departure = legs[0].get("origin", {}).get("plannedDateTime")
    arrival = legs[-1].get("destination", {}).get("plannedDateTime")
    if not departure or not arrival:
        return None
    from datetime import datetime

    dep = datetime.fromisoformat(departure)
    arr = datetime.fromisoformat(arrival)
    return round((arr - dep).total_seconds() / 60, 1)


def is_remote_location(location: str) -> bool:
    """Check whether a job location string indicates remote/vague work.

    Args:
        location: Location string from a job listing.

    Returns:
        True if the location is remote or too vague for routing.
    """
    loc = location.lower().strip()
    # "Netherlands" or "Remote" as the entire location string → vague
    if loc in _EXACT_VAGUE:
        return True
    # "Thuiswerken" anywhere in the string → remote
    return any(kw in loc for kw in _SUBSTR_VAGUE)


def calculate_travel_times(
    job_location: str,
    config: Config,
    db: Database | None = None,
) -> tuple[list[TravelTime], bool, float | None]:
    """Calculate travel times from home to the job location.

    Parallelizes car/bike routing and public transport API calls.
    Results are cached per-user to avoid repeated network calls.

    Args:
        job_location: Location string from the job listing.
        config: Application configuration with API keys and home address.
        db: Optional database for caching geocode and travel time results.

    Returns:
        Tuple of (travel_times list, location_unknown flag, distance_km).
    """
    if is_remote_location(job_location):
        logger.info(f"Remote/vague location '{job_location}' — skipping travel time")
        return [], True, None

    home_coords = _geocode(config.home_address, db, config.geocode_cache_days)
    if not home_coords:
        logger.warning(f"Could not geocode home address: {config.home_address}")
        return [], False, None

    job_coords = _geocode(job_location, db, config.geocode_cache_days)
    if not job_coords:
        logger.info(f"Could not geocode '{job_location}' — marking location unknown")
        return [], True, None

    distance = round(_haversine_km(home_coords, job_coords), 1)

    # Parallelize car/bike travel time calculation and NS travel time
    # These are independent HTTP requests to different APIs
    travel_times: list[TravelTime] = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_car_bike = executor.submit(
            _car_bike_travel_times,
            home_coords,
            job_coords,
            config,
            db,
        )
        future_ns = executor.submit(
            _ns_travel_time, home_coords, job_coords, config, db
        )

        travel_times.extend(future_car_bike.result())
        travel_times.append(future_ns.result())

    return travel_times, False, distance


def _car_bike_travel_times(
    home: tuple[float, float],
    job: tuple[float, float],
    config: Config,
    db: Database | None = None,
) -> list[TravelTime]:
    """Build car and bike TravelTime objects via OSRM (free) or ORS.

    Uses the free OSRM public server by default. Falls back to ORS
    if an API key is configured and OSRM fails. Parallelizes OSRM requests.

    Args:
        home: Home coordinates (lon, lat).
        job: Job coordinates (lon, lat).
        config: Application configuration.
        db: Optional database for caching travel time results.

    Returns:
        List of TravelTime for CAR and BIKE modes.
    """
    # Parallelize OSRM requests for car and bike (independent HTTP calls)
    car_min = None
    bike_min = None

    with ThreadPoolExecutor(max_workers=2) as executor:
        future_car = executor.submit(
            _get_osrm_time,
            home,
            job,
            "driving",
            db,
            config.travel_cache_days,
        )
        future_bike = executor.submit(
            _get_osrm_time,
            home,
            job,
            "bike",
            db,
            config.travel_cache_days,
        )

        car_min = future_car.result()
        bike_min = future_bike.result()

    # Fallback to ORS if configured and OSRM failed
    if config.ors_api_key:
        if car_min is None:
            car_min = _get_ors_time(
                home,
                job,
                "driving-car",
                config.ors_api_key,
                db,
                config.travel_cache_days,
            )
        if bike_min is None:
            bike_min = _get_ors_time(
                home,
                job,
                "cycling-regular",
                config.ors_api_key,
                db,
                config.travel_cache_days,
            )
    return [
        TravelTime(
            mode=TravelMode.CAR,
            minutes=car_min,
            available=car_min is not None,
            error=None if car_min is not None else "Routing failed",
        ),
        TravelTime(
            mode=TravelMode.BIKE,
            minutes=bike_min,
            available=bike_min is not None,
            error=None if bike_min is not None else "Routing failed",
        ),
    ]


def _ns_travel_time(
    home: tuple[float, float],
    job: tuple[float, float],
    config: Config,
    db: Database | None = None,
) -> TravelTime:
    """Build a public transport TravelTime object via NS API.

    Args:
        home: Home coordinates (lon, lat).
        job: Job coordinates (lon, lat).
        config: Application configuration.
        db: Optional database for caching travel time results.

    Returns:
        TravelTime for PUBLIC_TRANSPORT mode.
    """
    if not config.ns_api_key:
        logger.debug("No NS API key — skipping public transport travel time")
        return TravelTime(
            mode=TravelMode.PUBLIC_TRANSPORT, available=False, error="No NS API key"
        )

    pt_min = _get_ns_time(home, job, config.ns_api_key, db, config.travel_cache_days)
    return TravelTime(
        mode=TravelMode.PUBLIC_TRANSPORT,
        minutes=pt_min,
        available=pt_min is not None,
        error=None if pt_min is not None else "NS API request failed",
    )


def is_within_travel_limits(
    job_location: str | None,
    travel_times: list[TravelTime],
    config: Config,
    location_unknown: bool,
    distance_km: float | None = None,
) -> bool:
    """Check whether a job passes the configured travel-time filters.

    A job passes if at least one transport mode is within its limit,
    or if the straight-line distance is within max_distance_km.
    Jobs with unknown locations always pass.

    Args:
        job_location: Job location string (may be None).
        travel_times: List of calculated travel times.
        config: Configuration with max_travel_* and max_distance_km.
        location_unknown: True if location could not be geocoded.
        distance_km: Straight-line distance in km (None if unknown).

    Returns:
        True if the job passes the travel filter.
    """
    if location_unknown or not job_location:
        return True

    # Check travel time APIs first
    limits = {
        TravelMode.CAR: config.max_travel_car,
        TravelMode.PUBLIC_TRANSPORT: config.max_travel_pt,
        TravelMode.BIKE: config.max_travel_bike,
    }

    has_time_data = False
    for tt in travel_times:
        if not tt.available or tt.minutes is None:
            continue
        has_time_data = True
        limit = limits.get(tt.mode)
        if limit is not None and tt.minutes <= limit:
            return True

    if has_time_data:
        return False

    # Fallback: straight-line distance when no travel time APIs available
    if (
        distance_km is not None
        and config.max_distance_km is not None
        and distance_km > config.max_distance_km
    ):
        logger.info(
            f"Too far ({distance_km} km > {config.max_distance_km} km): {job_location}"
        )
        return False

    return True
