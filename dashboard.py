#!/usr/bin/env python3
"""Streamlit-dashboard til cykeltid fra hjem til valgt gymnasium."""

from __future__ import annotations

import json
import math
import pathlib

import streamlit as st
import folium
from streamlit_folium import st_folium

from osrm_bike_time import (
    OSRM_BASE_URL,
    RouteLookupError,
    format_duration,
    geocode_denmark_address,
    get_bike_route_time_seconds,
)

_DATA_FILE = pathlib.Path(__file__).parent / "gymnasier.json"
_raw = json.loads(_DATA_FILE.read_text(encoding="utf-8"))
# {name: address} and {name: region} dicts, sorted alphabetically
GYMNASIUMS: dict[str, str] = {entry["name"]: entry["address"] for entry in _raw}
GYMNASIUMS_REGION: dict[str, str] = {entry["name"]: entry["region"] for entry in _raw}
# Pre-loaded coordinates – avoids geocoding every school at runtime
GYMNASIUMS_COORDS: dict[str, tuple[float, float]] = {
    entry["name"]: (entry["lat"], entry["lon"])
    for entry in _raw
    if "lat" in entry and "lon" in entry
}

REGION_ORDER = [
    "Region Hovedstaden",
    "Region Sjælland",
    "Region Syddanmark",
    "Region Midtjylland",
    "Region Nordjylland",
]
ALL_REGIONS_LABEL = "Alle regioner"

_POSTCODE_REGIONS = [
    (1000, 3699, "Region Hovedstaden"),
    (3700, 3799, "Region Hovedstaden"),
    (3800, 4999, "Region Sjælland"),
    (5000, 6999, "Region Syddanmark"),
    (7000, 8999, "Region Midtjylland"),
    (9000, 9999, "Region Nordjylland"),
]


def postcode_to_region(pc: int) -> str:
    for lo, hi, region in _POSTCODE_REGIONS:
        if lo <= pc <= hi:
            return region
    return ALL_REGIONS_LABEL

DEFAULT_BIKE_SPEED_KMH = 16.0
BIKE_SPEED_ALERT_THRESHOLD_KMH = 25.0

ROUTING_SERVICE_PRESETS = {
    "OSRM demo bil (srv=0)": {"osrm_url": "https://routing.openstreetmap.de/routed-car", "profile": "driving"},
    "OSRM demo cykel (srv=1)": {"osrm_url": "https://routing.openstreetmap.de/routed-bike", "profile": "driving"},
    "OSRM demo gang (srv=2)": {"osrm_url": "https://routing.openstreetmap.de/routed-foot", "profile": "driving"},
    "Brugerdefineret server": {"osrm_url": OSRM_BASE_URL, "profile": "bike"},
}

DISCLAIMER = (
    "⚠️ **Ansvarsfraskrivelse:** Dette er ikke en officiel hjemmeside og har ingen tilknytning til "
    "nogen uddannelsesinstitution eller offentlig myndighed. Rejsetider og afstande er vejledende "
    "og beregnet på baggrund af tredjepartsdata. Der gives ingen garantier for nøjagtighed, "
    "fuldstændighed eller egnethed til et bestemt formål. Brug ikke denne side som grundlag for "
    "officielle beslutninger. Kortdata © OpenStreetMap-bidragydere."
)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Returner afstand i km (luftlinje) mellem to WGS-84-punkter."""
    r = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lon = math.radians(lon2 - lon1)
    a = (
        math.sin(d_lat / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(d_lon / 2) ** 2
    )
    return r * 2 * math.asin(math.sqrt(a))


@st.cache_data(show_spinner=False)
def geocode_address_cached(address: str) -> tuple[float, float, str]:
    return geocode_denmark_address(address)


@st.cache_data(show_spinner=False)
def detect_region_cached(address: str) -> str:
    """Geocode address and return the Danish region based on postcode."""
    import re as _re
    import urllib.parse as _up
    import urllib.request as _ur
    params = {
        "q": address,
        "format": "jsonv2",
        "limit": 1,
        "countrycodes": "dk",
        "addressdetails": 1,
    }
    url = f"https://nominatim.openstreetmap.org/search?{_up.urlencode(params)}"
    req = _ur.Request(url, headers={"User-Agent": "gym-dashboard/1.0 (educational)"})
    with _ur.urlopen(req, timeout=10) as r:
        data = json.loads(r.read().decode())
    if not data:
        return ALL_REGIONS_LABEL
    pc_str = data[0].get("address", {}).get("postcode", "")
    digits = _re.sub(r"\D", "", pc_str)[:4]
    if not digits:
        return ALL_REGIONS_LABEL
    return postcode_to_region(int(digits))


@st.cache_data(show_spinner=False)
def geocode_all_gymnasiums() -> dict[str, tuple[float, float]]:
    coords: dict[str, tuple[float, float]] = {}
    for name, address in GYMNASIUMS.items():
        try:
            lat, lon, _ = geocode_denmark_address(address)
            coords[name] = (lat, lon)
        except Exception:  # noqa: BLE001
            pass
    return coords


def nearest_gymnasiums(
    home_lat: float, home_lon: float, region: str | None = None, n: int = 5
) -> list[tuple[str, float]]:
    """Return the *n* nearest schools.

    Strategy (two-phase for speed):
    1. Euclidean distance on raw lat/lon to pick the top-10 candidates
       (no trig – O(schools) but very cheap per school).
    2. Precise haversine on those 10 candidates to produce the ranked top-n.
    """
    candidates = [
        (name, lat, lon)
        for name, (lat, lon) in GYMNASIUMS_COORDS.items()
        if not (region and region != ALL_REGIONS_LABEL)
        or GYMNASIUMS_REGION.get(name) == region
    ]

    # Phase 1 – cheap Euclidean pre-filter (unitless, but monotone with distance)
    pre_filter_n = max(n * 2, 10)
    candidates.sort(key=lambda t: (t[1] - home_lat) ** 2 + (t[2] - home_lon) ** 2)
    top_candidates = candidates[:pre_filter_n]

    # Phase 2 – precise haversine on the small shortlist
    ranked = [
        (name, haversine_km(home_lat, home_lon, lat, lon))
        for name, lat, lon in top_candidates
    ]
    ranked.sort(key=lambda x: x[1])
    return ranked[:n]


def main() -> None:
    st.set_page_config(page_title="Cykeltid til gymnasium", layout="wide")

    if "route_result" not in st.session_state:
        st.session_state.route_result = None
    if "last_geocoded_home" not in st.session_state:
        st.session_state.last_geocoded_home = ""
    if "region_select" not in st.session_state:
        st.session_state.region_select = ALL_REGIONS_LABEL

    st.title("🚲 Cykeltid til gymnasium")
    st.write("Indtast din hjemmeadresse og vælg et gymnasium for at beregne cykeltiden.")

    left_col, right_col = st.columns([1.05, 1.45], gap="large")

    with left_col:
        home_address = st.text_input(
            "Hjemmeadresse (Danmark)",
            placeholder="Eksempel: Nørrebrogade 1, 2200 København N",
            key="home_address_input",
        )

        # --- Auto-detect region when home address changes ---
        addr_stripped = home_address.strip()
        if addr_stripped and addr_stripped != st.session_state.last_geocoded_home:
            try:
                with st.spinner("Registrerer region..."):
                    detected = detect_region_cached(addr_stripped)
                st.session_state.region_select = detected
                st.session_state.last_geocoded_home = addr_stripped
            except Exception:  # noqa: BLE001
                st.session_state.last_geocoded_home = addr_stripped

        # --- Region selector ---
        region_options = [ALL_REGIONS_LABEL] + REGION_ORDER
        selected_region = st.selectbox(
            "Region",
            options=region_options,
            key="region_select",
        )

        # --- Nearest 5 table filtered by region ---
        nearest_list: list[tuple[str, float]] = []
        if addr_stripped:
            try:
                with st.spinner("Finder nærmeste gymnasier..."):
                    home_lat_pre, home_lon_pre, _ = geocode_address_cached(addr_stripped)
                nearest_list = nearest_gymnasiums(home_lat_pre, home_lon_pre, region=selected_region)
                region_label = selected_region if selected_region != ALL_REGIONS_LABEL else "Danmark"
                st.markdown(f"**5 nærmeste gymnasier i {region_label} (luftlinje)**")
                st.table(
                    {
                        "Gymnasium": [name for name, _ in nearest_list],
                        "Luftlinjeafstand": [f"{dist:.1f} km" for _, dist in nearest_list],
                    }
                )
            except Exception:  # noqa: BLE001
                pass

        # --- Gymnasium selectbox filtered by region ---
        if selected_region and selected_region != ALL_REGIONS_LABEL:
            gym_options = [
                name for name in GYMNASIUMS
                if GYMNASIUMS_REGION.get(name) == selected_region
            ]
        else:
            gym_options = list(GYMNASIUMS.keys())

        with st.form("route_form"):
            default_gym = nearest_list[0][0] if nearest_list and nearest_list[0][0] in gym_options else gym_options[0]
            gymnasium_name = st.selectbox(
                "Vælg gymnasium",
                options=gym_options,
                index=gym_options.index(default_gym),
            )

            with st.expander("Avancerede indstillinger"):
                service_name = st.selectbox(
                    "Ruteplanlægningstjeneste",
                    options=list(ROUTING_SERVICE_PRESETS.keys()),
                    index=1,
                )

                selected_service = ROUTING_SERVICE_PRESETS[service_name]
                if service_name == "Brugerdefineret server":
                    osrm_url = st.text_input("OSRM URL", value=selected_service["osrm_url"])
                    profile = st.selectbox("Ruteprofil", options=["bike", "driving", "foot"], index=0)
                else:
                    osrm_url = selected_service["osrm_url"]
                    profile = selected_service["profile"]
                    st.caption(f"Bruger {service_name}: {osrm_url} (profil={profile})")

            calculate_clicked = st.form_submit_button("Beregn cykeltid")

    if calculate_clicked:
        if not home_address.strip():
            with left_col:
                st.warning("Indtast venligst en hjemmeadresse.")
            return

        destination_address = GYMNASIUMS[gymnasium_name]

        with st.spinner("Beregner rute..."):
            try:
                home_lat, home_lon, home_display = geocode_address_cached(home_address)
                school_lat, school_lon, school_display = geocode_address_cached(destination_address)

                duration_s, distance_m, route_coords = get_bike_route_time_seconds(
                    home_lat,
                    home_lon,
                    school_lat,
                    school_lon,
                    osrm_base_url=osrm_url,
                    profile=profile,
                )
            except RouteLookupError as exc:
                with left_col:
                    st.error(str(exc))
                    st.info("Tip: Prøv en anden OSRM-server eller skift profil til bil.")
                return
            except Exception as exc:  # noqa: BLE001
                with left_col:
                    st.error(f"Uventet fejl: {exc}")
                return

        avg_speed_kmh = (distance_m / 1000) / (duration_s / 3600) if duration_s > 0 else 0.0
        bike_speed_kmh = DEFAULT_BIKE_SPEED_KMH
        estimated_bike_duration_s = distance_m / (bike_speed_kmh * 1000 / 3600)
        used_estimated_duration = profile == "bike" and avg_speed_kmh > BIKE_SPEED_ALERT_THRESHOLD_KMH

        st.session_state.route_result = {
            "home_display": home_display,
            "school_display": school_display,
            "duration_s": duration_s,
            "distance_m": distance_m,
            "route_coords": route_coords,
            "home_lat": home_lat,
            "home_lon": home_lon,
            "school_lat": school_lat,
            "school_lon": school_lon,
            "profile": profile,
            "service_name": service_name,
            "avg_speed_kmh": avg_speed_kmh,
            "estimated_bike_duration_s": estimated_bike_duration_s,
            "used_estimated_duration": used_estimated_duration,
            "bike_speed_kmh": bike_speed_kmh,
        }

    result = st.session_state.route_result
    if result:
        with left_col:
            st.success("Rute fundet")
            st.write(f"Fra: {result['home_display']}")
            st.write(f"Til: {result['school_display']}")

            if result["used_estimated_duration"]:
                st.warning(
                    f"OSRM returnerede en meget høj gennemsnitshastighed ({result['avg_speed_kmh']:.1f} km/t). "
                    f"Cykeltiden er estimeret ud fra afstand ved {result['bike_speed_kmh']:.1f} km/t."
                )

            info_col1, info_col2 = st.columns(2)
            displayed_duration = (
                result["estimated_bike_duration_s"]
                if result["used_estimated_duration"]
                else result["duration_s"]
            )
            duration_label = "Estimeret cykeltid" if result["used_estimated_duration"] else "Rejsetid"

            info_col1.metric(duration_label, format_duration(displayed_duration))
            info_col2.metric("Afstand", f"{result['distance_m'] / 1000:.2f} km")
            st.caption(
                f"Tjeneste: {result['service_name']} | "
                f"Profil: {result['profile']} | "
                f"Gns. hastighed: {result['avg_speed_kmh']:.1f} km/t"
            )

        route_coords = result["route_coords"]
        if route_coords:
            m = folium.Map(
                location=route_coords[0],
                zoom_start=14,
                tiles="CartoDB Positron",
                control_scale=True,
            )

            folium.PolyLine(
                route_coords,
                color="#1e88e5",
                weight=5,
                opacity=0.95,
                popup="Cykelrute",
            ).add_to(m)

            folium.CircleMarker(
                [result["home_lat"], result["home_lon"]],
                radius=7,
                color="#00a152",
                fill=True,
                fill_color="#00a152",
                fill_opacity=1.0,
                popup="Hjem",
            ).add_to(m)

            folium.CircleMarker(
                [result["school_lat"], result["school_lon"]],
                radius=7,
                color="#d32f2f",
                fill=True,
                fill_color="#d32f2f",
                fill_opacity=1.0,
                popup="Gymnasium",
            ).add_to(m)

            m.fit_bounds(route_coords)

            with right_col:
                st.subheader("Rutekort")
                st_folium(m, use_container_width=True, height=620)
    else:
        with right_col:
            st.info("Beregn en rute for at vise kortet her.")

    # Sidefod med ansvarsfraskrivelse
    st.divider()
    st.caption(DISCLAIMER)


if __name__ == "__main__":
    main()
