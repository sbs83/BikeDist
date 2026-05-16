#!/usr/bin/env python3
"""Get bike travel time between two addresses in Denmark using OSRM."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import urllib.request
from typing import Tuple

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
OSRM_BASE_URL = "https://router.project-osrm.org"


class RouteLookupError(Exception):
    """Raised when geocoding or routing fails."""


def http_get_json(url: str, timeout: int = 20) -> dict | list:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": "osrm-bike-time-script/1.0 (educational example)",
            "Accept": "application/json",
        },
    )

    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def geocode_denmark_address(address: str) -> Tuple[float, float, str]:
    params = {
        "q": address,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "dk",
        "addressdetails": 1,
    }
    url = f"{NOMINATIM_URL}?{urllib.parse.urlencode(params)}"
    data = http_get_json(url)

    if not data:
        raise RouteLookupError(f"Could not geocode address in Denmark: {address}")

    top = data[0]
    lat = float(top["lat"])
    lon = float(top["lon"])
    display_name = top.get("display_name", address)
    return lat, lon, display_name


def get_bike_route_time_seconds(
    start_lat: float,
    start_lon: float,
    end_lat: float,
    end_lon: float,
    osrm_base_url: str,
    profile: str = "bike",
) -> tuple[float, float, list]:
    # OSRM route format is lon,lat;lon,lat
    coords = f"{start_lon},{start_lat};{end_lon},{end_lat}"
    params = {
        "overview": "full",
        "alternatives": "false",
        "steps": "false",
        "geometries": "geojson",
    }
    url = (
        f"{osrm_base_url.rstrip('/')}/route/v1/{profile}/{coords}"
        f"?{urllib.parse.urlencode(params)}"
    )

    data = http_get_json(url)

    if data.get("code") != "Ok" or not data.get("routes"):
        message = data.get("message", "Unknown OSRM error")
        raise RouteLookupError(
            f"OSRM could not compute a '{profile}' route. Server message: {message}"
        )

    route = data["routes"][0]
    geometry = route.get("geometry", {}).get("coordinates", [])
    # Swap lon,lat to lat,lon for plotting
    route_coords = [[lat, lon] for lon, lat in geometry]
    return float(route["duration"]), float(route["distance"]), route_coords


def format_duration(seconds: float) -> str:
    total = int(round(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)

    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Find bike travel time between two addresses in Denmark using OSRM."
    )
    parser.add_argument("from_address", help="Start address in Denmark")
    parser.add_argument("to_address", help="End address in Denmark")
    parser.add_argument(
        "--osrm-url",
        default=OSRM_BASE_URL,
        help=f"OSRM base URL (default: {OSRM_BASE_URL})",
    )
    parser.add_argument(
        "--profile",
        default="bike",
        help="OSRM profile to use (default: bike)",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    try:
        start_lat, start_lon, start_name = geocode_denmark_address(args.from_address)
        end_lat, end_lon, end_name = geocode_denmark_address(args.to_address)

        duration_s, distance_m, route_coords = get_bike_route_time_seconds(
            start_lat,
            start_lon,
            end_lat,
            end_lon,
            osrm_base_url=args.osrm_url,
            profile=args.profile,
        )

        print(f"From: {start_name}")
        print(f"To:   {end_name}")
        print(f"Distance: {distance_m / 1000:.2f} km")
        print(f"Bike travel time: {format_duration(duration_s)} ({duration_s / 60:.1f} min)")
        return 0
    except RouteLookupError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        print(
            "Tip: The default public OSRM server may not support bike profile in all setups. "
            "Try another OSRM instance or use --profile driving for testing.",
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # noqa: BLE001
        print(f"Unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
