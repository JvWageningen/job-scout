"""Tests for travel time logic (filter and helper functions)."""

from __future__ import annotations

import pytest

from job_scout.models import Config, TravelMode, TravelTime
from job_scout.travel import is_remote_location, is_within_travel_limits


def test_remote_location_keywords() -> None:
    """Known remote/vague location strings are detected correctly."""
    assert is_remote_location("Remote") is True
    assert is_remote_location("REMOTE") is True
    assert is_remote_location("Netherlands") is True
    assert is_remote_location("Nederland") is True
    assert is_remote_location("Thuiswerken") is True


def test_non_remote_location() -> None:
    """Specific city names are not flagged as remote."""
    assert is_remote_location("Amsterdam") is False
    assert is_remote_location("Rotterdam") is False
    assert is_remote_location("Utrecht, Netherlands") is False


def test_within_limits_car_passes(base_config: Config) -> None:
    """Job passes when car travel is within the 30-minute limit."""
    travel_times = [
        TravelTime(mode=TravelMode.CAR, minutes=25.0),
        TravelTime(mode=TravelMode.PUBLIC_TRANSPORT, minutes=90.0),
    ]
    result = is_within_travel_limits("Amsterdam", travel_times, base_config, False)
    assert result is True


def test_within_limits_pt_passes(base_config: Config) -> None:
    """Job passes when public transport travel is within the 60-minute limit."""
    travel_times = [
        TravelTime(mode=TravelMode.CAR, minutes=35.0),
        TravelTime(mode=TravelMode.PUBLIC_TRANSPORT, minutes=55.0),
    ]
    result = is_within_travel_limits("Rotterdam", travel_times, base_config, False)
    assert result is True


def test_exceeds_all_limits_rejected(base_config: Config) -> None:
    """Job is rejected when all travel times exceed their limits."""
    travel_times = [
        TravelTime(mode=TravelMode.CAR, minutes=45.0),
        TravelTime(mode=TravelMode.PUBLIC_TRANSPORT, minutes=90.0),
        TravelTime(mode=TravelMode.BIKE, minutes=120.0),
    ]
    result = is_within_travel_limits("Eindhoven", travel_times, base_config, False)
    assert result is False


def test_unknown_location_always_passes(base_config: Config) -> None:
    """Jobs with unknown location pass the travel filter unconditionally."""
    assert is_within_travel_limits("Somewhere", [], base_config, True) is True


def test_no_travel_data_passes(base_config: Config) -> None:
    """When no travel data is available, the job passes by default."""
    assert is_within_travel_limits("Amsterdam", [], base_config, False) is True


def test_all_unavailable_travel_times_pass(base_config: Config) -> None:
    """When all modes are unavailable, the job passes (API was down)."""
    travel_times = [
        TravelTime(mode=TravelMode.CAR, available=False, error="API error"),
        TravelTime(mode=TravelMode.PUBLIC_TRANSPORT, available=False, error="No key"),
    ]
    result = is_within_travel_limits("Amsterdam", travel_times, base_config, False)
    assert result is True


def test_none_location_passes(base_config: Config) -> None:
    """Job with no location passes the travel filter."""
    assert is_within_travel_limits(None, [], base_config, False) is True


def test_distance_filter_rejects_too_far(base_config: Config) -> None:
    """Job is rejected when distance exceeds max_distance_km."""
    base_config.max_distance_km = 50
    result = is_within_travel_limits(
        "Eindhoven", [], base_config, False, distance_km=120.0
    )
    assert result is False


def test_distance_filter_passes_within_limit(base_config: Config) -> None:
    """Job passes when distance is within max_distance_km."""
    base_config.max_distance_km = 50
    result = is_within_travel_limits(
        "Haarlem", [], base_config, False, distance_km=30.0
    )
    assert result is True


def test_distance_filter_not_applied_when_unconfigured(base_config: Config) -> None:
    """Distance filter does not reject when max_distance_km is None."""
    base_config.max_distance_km = None
    result = is_within_travel_limits(
        "Eindhoven", [], base_config, False, distance_km=200.0
    )
    assert result is True


def test_travel_time_takes_priority_over_distance(base_config: Config) -> None:
    """Travel time data is used when available, even with distance set."""
    base_config.max_distance_km = 10
    travel_times = [TravelTime(mode=TravelMode.CAR, minutes=25.0)]
    result = is_within_travel_limits(
        "Amsterdam", travel_times, base_config, False, distance_km=50.0
    )
    assert result is True


# ---------------------------------------------------------------------------
# _parse_ns_duration
# ---------------------------------------------------------------------------


def test_parse_ns_duration_explicit_minutes() -> None:
    """_parse_ns_duration uses plannedDurationInMinutes when available."""
    from job_scout.travel import _parse_ns_duration

    trip = {"plannedDurationInMinutes": 42}
    assert _parse_ns_duration(trip) == 42.0


def test_parse_ns_duration_from_leg_datetimes() -> None:
    """_parse_ns_duration computes duration from first/last leg datetimes."""
    from job_scout.travel import _parse_ns_duration

    trip = {
        "legs": [
            {"origin": {"plannedDateTime": "2024-06-01T08:00:00"}, "destination": {}},
            {"origin": {}, "destination": {"plannedDateTime": "2024-06-01T09:30:00"}},
        ]
    }
    result = _parse_ns_duration(trip)
    assert result == 90.0


def test_parse_ns_duration_empty_legs_returns_none() -> None:
    """_parse_ns_duration returns None when legs list is empty."""
    from job_scout.travel import _parse_ns_duration

    assert _parse_ns_duration({"legs": []}) is None


def test_parse_ns_duration_missing_datetimes_returns_none() -> None:
    """_parse_ns_duration returns None when departure/arrival datetimes are absent."""
    from job_scout.travel import _parse_ns_duration

    trip = {
        "legs": [
            {"origin": {}, "destination": {}},
        ]
    }
    assert _parse_ns_duration(trip) is None


# ---------------------------------------------------------------------------
# _car_bike_travel_times and _ns_travel_time
# ---------------------------------------------------------------------------


def test_car_bike_uses_osrm_for_car_and_estimates_bike(
    base_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Car comes from OSRM; bike is estimated from distance, not OSRM car time.

    Regression: the free OSRM 'bike' profile returns car times, so bike must be
    derived from distance instead of mirroring the car figure.
    """
    from job_scout.models import TravelMode
    from job_scout.travel import _car_bike_travel_times

    base_config.ors_api_key = None
    monkeypatch.setattr("job_scout.travel._get_osrm_time", lambda *_a: 25.0)
    result = _car_bike_travel_times((4.9, 52.4), (4.8, 52.3), base_config, 40.0)

    assert len(result) == 2
    assert all(tt.available for tt in result)
    car = next(tt for tt in result if tt.mode == TravelMode.CAR)
    bike = next(tt for tt in result if tt.mode == TravelMode.BIKE)
    assert car.minutes == 25.0
    # 40 km at 20 km/h = 120 min — must not equal the 25 min car time.
    assert bike.minutes == 120.0
    assert bike.minutes != car.minutes


def test_estimate_bike_minutes() -> None:
    """_estimate_bike_minutes converts distance to cycling time; None passes through."""
    from job_scout.travel import _estimate_bike_minutes

    assert _estimate_bike_minutes(20.0) == 60.0
    assert _estimate_bike_minutes(40.0) == 120.0
    assert _estimate_bike_minutes(None) is None


def test_car_bike_falls_back_to_ors(
    base_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_car_bike_travel_times falls back to ORS when OSRM fails."""
    from job_scout.travel import _car_bike_travel_times

    base_config.ors_api_key = "test-key"
    monkeypatch.setattr("job_scout.travel._get_osrm_time", lambda *_a: None)
    monkeypatch.setattr("job_scout.travel._get_ors_time", lambda *_a: 30.0)
    result = _car_bike_travel_times((4.9, 52.4), (4.8, 52.3), base_config)
    assert len(result) == 2
    assert all(tt.available for tt in result)
    assert result[0].minutes == 30.0


def test_car_bike_unavailable_when_both_fail(
    base_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """_car_bike_travel_times returns unavailable when both OSRM and ORS fail."""
    from job_scout.travel import _car_bike_travel_times

    base_config.ors_api_key = None
    monkeypatch.setattr("job_scout.travel._get_osrm_time", lambda *_a: None)
    result = _car_bike_travel_times((4.9, 52.4), (4.8, 52.3), base_config)
    assert len(result) == 2
    assert all(not tt.available for tt in result)


def test_ns_travel_time_no_api_key(base_config: Config) -> None:
    """_ns_travel_time returns an unavailable TravelTime when NS key is missing."""
    from job_scout.travel import _ns_travel_time

    base_config.ns_api_key = None
    result = _ns_travel_time((4.9, 52.4), (4.8, 52.3), base_config)
    assert result.available is False
    assert result.error is not None


# ---------------------------------------------------------------------------
# geocode + travel time integration (mocked network)
# ---------------------------------------------------------------------------


def test_calculate_travel_times_remote_location(base_config: Config) -> None:
    """calculate_travel_times skips geocoding for remote locations."""
    from job_scout.travel import calculate_travel_times

    times, unknown, distance = calculate_travel_times("Remote", base_config)
    assert times == []
    assert unknown is True
    assert distance is None


def test_calculate_travel_times_geocode_failure(
    base_config: Config, monkeypatch: pytest.MonkeyPatch
) -> None:
    """calculate_travel_times marks location unknown when geocoding fails."""
    from job_scout.travel import calculate_travel_times

    monkeypatch.setattr("job_scout.travel._geocode", lambda *_a, **_kw: None)
    times, unknown, distance = calculate_travel_times("Fake City XYZ", base_config)
    # Home geocode failed — no times returned
    assert times == []
    assert distance is None


def test_geocode_cache_hit_skips_network(
    base_config: Config, tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second geocode call with identical address uses cache and skips network."""
    from unittest.mock import Mock

    from job_scout.travel import _geocode

    # Mock the HTTP request
    mock_request = Mock()
    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        mock_request.return_value.json.return_value = [
            {"lon": "4.9041", "lat": "52.3676"}
        ]
        mock_request.return_value.raise_for_status = Mock()
        return mock_request.return_value

    monkeypatch.setattr("job_scout.travel.requests.get", mock_get)

    address = "Amsterdam, Netherlands"

    # First call should hit the network
    result1 = _geocode(address, tmp_db, cache_days=90)
    assert result1 == (4.9041, 52.3676)
    assert call_count == 1

    # Second call should use cache, not hit network
    result2 = _geocode(address, tmp_db, cache_days=90)
    assert result2 == (4.9041, 52.3676)
    assert call_count == 1  # Still 1, cache was used


def test_travel_time_cache_hit_skips_network(
    base_config: Config, tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Second travel time call with identical route uses cache and skips network."""
    from unittest.mock import Mock

    from job_scout.travel import _get_osrm_time

    # Mock the HTTP request
    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = Mock()
        resp.json.return_value = {
            "code": "Ok",
            "routes": [{"duration": 2700}],  # 45 minutes
        }
        resp.raise_for_status = Mock()
        return resp

    monkeypatch.setattr("job_scout.travel.requests.get", mock_get)

    origin = (4.9041, 52.3676)
    destination = (5.2913, 52.1326)

    # First call should hit the network
    result1 = _get_osrm_time(origin, destination, "driving", tmp_db, cache_days=14)
    assert result1 == 45.0
    assert call_count == 1

    # Second call should use cache, not hit network
    result2 = _get_osrm_time(origin, destination, "driving", tmp_db, cache_days=14)
    assert result2 == 45.0
    assert call_count == 1  # Still 1, cache was used


def test_address_change_bypasses_geocode_cache(
    base_config: Config, tmp_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Changing home address naturally bypasses geocode cache (different key)."""
    from unittest.mock import Mock

    from job_scout.travel import _geocode

    call_count = 0

    def mock_get(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        resp = Mock()
        if call_count == 1:
            # First address
            resp.json.return_value = [{"lon": "4.9041", "lat": "52.3676"}]
        else:
            # Second address
            resp.json.return_value = [{"lon": "13.4050", "lat": "52.5200"}]
        resp.raise_for_status = Mock()
        return resp

    monkeypatch.setattr("job_scout.travel.requests.get", mock_get)

    # First address
    result1 = _geocode("Amsterdam, Netherlands", tmp_db, cache_days=90)
    assert result1 == (4.9041, 52.3676)
    assert call_count == 1

    # Different address — cache miss, hits network
    result2 = _geocode("Berlin, Germany", tmp_db, cache_days=90)
    assert result2 == (13.4050, 52.5200)
    assert call_count == 2

    # Back to first address — uses cache
    result3 = _geocode("Amsterdam, Netherlands", tmp_db, cache_days=90)
    assert result3 == (4.9041, 52.3676)
    assert call_count == 2  # Still 2, cache was used
