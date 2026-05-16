#!/usr/bin/env python3
"""Build gymnasier.json from danskegymnasier.dk with lat/lon coordinates."""

from __future__ import annotations

import argparse
import html
import json
import pathlib
import re
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from html.parser import HTMLParser

SOURCE_URL = "https://danskegymnasier.dk/find-gymnasier/"
DEFAULT_OUTPUT = "gymnasier.json"
USER_AGENT = "BikeDist gymnasier builder/1.0 (educational)"

REGION_ORDER = [
    "Region Hovedstaden",
    "Region Sj\u00e6lland",
    "Region Syddanmark",
    "Region Midtjylland",
    "Region Nordjylland",
]

POSTCODE_REGIONS = [
    (1000, 3699, "Region Hovedstaden"),
    (3700, 3799, "Region Hovedstaden"),
    (3800, 4999, "Region Sj\u00e6lland"),
    (5000, 6999, "Region Syddanmark"),
    (7000, 8999, "Region Midtjylland"),
    (9000, 9999, "Region Nordjylland"),
]

ROLE_PREFIXES = (
    "Rektor",
    "Konst. rektor",
    "Konst. uddannelsesleder",
    "Uddannelsesdirekt\u00f8r",
    "Uddannelsesleder",
)

NOISE_PREFIXES = (
    "tlf",
    "telefon",
    "sikker mail",
    "sikkermail",
    "mail",
    "www",
)


@dataclass
class SchoolRaw:
    name: str
    text_lines: list[str]


@dataclass
class MarkerRaw:
    name: str
    lat: float
    lon: float


class SchoolListParser(HTMLParser):
    """Parse loop items from the Toolset list on the source page."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.schools: list[SchoolRaw] = []

        self._inside_item = False
        self._item_div_depth = 0
        self._current_name: list[str] = []
        self._current_text: list[str] = []

        self._in_h4 = False
        self._in_p = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()

        if tag == "div" and "wpv-block-loop-item" in classes:
            self._inside_item = True
            self._item_div_depth = 1
            self._current_name = []
            self._current_text = []
            self._in_h4 = False
            self._in_p = False
            return

        if not self._inside_item:
            return

        if tag == "div":
            self._item_div_depth += 1
        elif tag == "h4":
            self._in_h4 = True
        elif tag == "p":
            self._in_p = True
            self._current_text.append("\n")
        elif tag == "br" and self._in_p:
            self._current_text.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._inside_item:
            return

        if tag == "h4":
            self._in_h4 = False
        elif tag == "p":
            self._in_p = False
            self._current_text.append("\n")
        elif tag == "div":
            self._item_div_depth -= 1
            if self._item_div_depth == 0:
                self._flush_item()

    def handle_data(self, data: str) -> None:
        if not self._inside_item:
            return

        if self._in_h4:
            self._current_name.append(data)
        elif self._in_p:
            self._current_text.append(data)

    def _flush_item(self) -> None:
        name = normalize_space("".join(self._current_name))
        if not name:
            self._inside_item = False
            return

        text = html.unescape("".join(self._current_text).replace("\xa0", " "))
        lines = [normalize_space(line) for line in text.splitlines()]
        lines = [line for line in lines if line]

        self.schools.append(SchoolRaw(name=name, text_lines=lines))

        self._inside_item = False
        self._item_div_depth = 0
        self._current_name = []
        self._current_text = []


class MarkerParser(HTMLParser):
    """Parse hidden map marker nodes that carry exact coordinates."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.markers: list[MarkerRaw] = []

        self._inside_marker = False
        self._div_depth = 0
        self._buffer: list[str] = []
        self._lat: float | None = None
        self._lon: float | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_map = dict(attrs)
        classes = (attr_map.get("class") or "").split()

        if tag == "div" and "js-wpv-addon-maps-marker" in classes:
            try:
                self._lat = float(attr_map.get("data-markerlat") or "")
                self._lon = float(attr_map.get("data-markerlon") or "")
            except ValueError:
                self._lat = None
                self._lon = None

            self._inside_marker = self._lat is not None and self._lon is not None
            self._div_depth = 1
            self._buffer = []
            return

        if self._inside_marker:
            if tag == "div":
                self._div_depth += 1
            elif tag == "br":
                self._buffer.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if not self._inside_marker:
            return

        if tag == "div":
            self._div_depth -= 1
            if self._div_depth == 0:
                self._flush_marker()

    def handle_data(self, data: str) -> None:
        if self._inside_marker:
            self._buffer.append(data)

    def _flush_marker(self) -> None:
        text = html.unescape("".join(self._buffer).replace("\xa0", " "))
        lines = [normalize_space(line) for line in text.splitlines() if normalize_space(line)]
        if lines and self._lat is not None and self._lon is not None:
            self.markers.append(MarkerRaw(name=lines[0], lat=self._lat, lon=self._lon))

        self._inside_marker = False
        self._div_depth = 0
        self._buffer = []
        self._lat = None
        self._lon = None


def normalize_space(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def normalize_name(name: str) -> str:
    name = html.unescape(name)
    name = normalize_space(name)
    return name.casefold()


def fetch_html(url: str) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=30) as response:
        return response.read().decode("utf-8", errors="replace")


def strip_contact_noise(text: str) -> str:
    text = text.strip(" ,")
    # Strip trailing contact information if present in same line.
    text = re.split(r"\b(Tlf\.?|Telefon|Sikker mail|Sikkermail|Mail|www\.|http|mailto:)\b", text, maxsplit=1)[0]
    return normalize_space(text)


def looks_like_role(line: str) -> bool:
    low = line.casefold()
    return any(low.startswith(prefix.casefold()) for prefix in ROLE_PREFIXES)


def looks_like_noise(line: str) -> bool:
    low = line.casefold()
    if "@" in low:
        return True
    return any(low.startswith(prefix) for prefix in NOISE_PREFIXES)


def postcode_to_region(postcode: int) -> str | None:
    for lo, hi, region in POSTCODE_REGIONS:
        if lo <= postcode <= hi:
            return region
    return None


def infer_region(lines: list[str], postcode: int | None) -> str:
    full = " ".join(lines).casefold()

    for region in REGION_ORDER:
        if region.casefold() in full:
            return region

    if any(token in full for token in ("gr\u00f8nland", "groenland", "kalaallit", "nuuk", "aasiaat", "qaqortoq")):
        return "Gr\u00f8nland"

    if any(token in full for token in ("f\u00e6r\u00f8", "foer\u00f8", "f\u00f8roy", "torshavn", "suduroy", "fuglaf")):
        return "F\u00e6r\u00f8erne"

    if any(token in full for token in ("slesvig", "flensborg", "deutschland", "tyskland", "sydslesvig")):
        return "Sydslesvig"

    if postcode is not None:
        mapped = postcode_to_region(postcode)
        if mapped:
            return mapped

    return "Ukendt"


def parse_address(lines: list[str]) -> tuple[str, int | None]:
    postcode_re = re.compile(r"\b(\d{4})\b")

    candidates = [line for line in lines if not looks_like_noise(line)]
    if not candidates:
        return "", None

    postcode = None
    postcode_idx = -1
    postcode_match: re.Match[str] | None = None

    for idx, line in enumerate(candidates):
        match = postcode_re.search(line)
        if match:
            postcode = int(match.group(1))
            postcode_idx = idx
            postcode_match = match
            break

    if postcode_match is None:
        # Fallback for non-DK addresses that may not have a 4-digit postcode.
        non_role = [line for line in candidates if not looks_like_role(line)]
        return (", ".join(non_role[:2]) if non_role else candidates[0], None)

    line = candidates[postcode_idx]
    before = normalize_space(line[: postcode_match.start()])
    after = normalize_space(line[postcode_match.end() :])
    after = strip_contact_noise(after)

    street = ""

    if postcode_idx > 0:
        prev = strip_contact_noise(candidates[postcode_idx - 1])
        if prev and not looks_like_role(prev) and not looks_like_noise(prev):
            street = prev

    if not street and before and not looks_like_role(before):
        street = before

    if not street:
        # Last resort: find something street-like before postcode in all text.
        merged = " ".join(candidates[: postcode_idx + 1])
        merged_before = normalize_space(merged[: merged.find(postcode_match.group(1))])
        chunks = [c.strip() for c in merged_before.split(" ") if c.strip()]
        if chunks:
            # Keep the last 8 tokens to avoid role names at the beginning.
            street = normalize_space(" ".join(chunks[-8:]))

    street = strip_contact_noise(street)
    city_part = strip_contact_noise(f"{postcode} {after}".strip())

    if street:
        return f"{street}, {city_part}", postcode
    return city_part, postcode


def geocode_with_nominatim(name: str, address: str, region: str, sleep_seconds: float) -> tuple[float | None, float | None]:
    queries = []

    if region in ("Gr\u00f8nland", "F\u00e6r\u00f8erne", "Sydslesvig"):
        queries.extend([
            f"{name}, {address}",
            address,
        ])
    else:
        queries.extend([
            f"{name}, {address}, Danmark",
            f"{address}, Danmark",
            f"{name}, {address}",
            address,
        ])

    for query in queries:
        params = {
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "addressdetails": 1,
        }
        url = f"https://nominatim.openstreetmap.org/search?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})

        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8", errors="replace"))
        except Exception:
            time.sleep(sleep_seconds)
            continue

        time.sleep(sleep_seconds)

        if payload:
            try:
                lat = float(payload[0]["lat"])
                lon = float(payload[0]["lon"])
                return lat, lon
            except Exception:
                continue

    return None, None


def build_dataset(source_url: str, limit: int | None, sleep_seconds: float) -> list[dict[str, object]]:
    html_text = fetch_html(source_url)

    marker_parser = MarkerParser()
    marker_parser.feed(html_text)
    marker_lookup = {normalize_name(marker.name): (marker.lat, marker.lon) for marker in marker_parser.markers}

    parser = SchoolListParser()
    parser.feed(html_text)

    schools = parser.schools
    if limit is not None:
        schools = schools[:limit]

    result: list[dict[str, object]] = []

    for idx, school in enumerate(schools, start=1):
        address, postcode = parse_address(school.text_lines)
        region = infer_region(school.text_lines, postcode)

        lat, lon = marker_lookup.get(normalize_name(school.name), (None, None))
        if lat is None or lon is None:
            lat, lon = geocode_with_nominatim(school.name, address, region, sleep_seconds)

        result.append(
            {
                "name": school.name,
                "address": address,
                "region": region,
                "lat": lat,
                "lon": lon,
            }
        )

        print(f"[{idx}/{len(schools)}] {school.name} -> lat={lat}, lon={lon}")

    result.sort(key=lambda item: str(item["name"]).casefold())
    return result


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build gymnasier.json from danskegymnasier.dk")
    parser.add_argument("--source-url", default=SOURCE_URL, help="Source URL with school list")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="Output JSON path")
    parser.add_argument("--limit", type=int, default=None, help="Only process first N schools")
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.0,
        help="Delay in seconds between geocoding requests (default: 1.0)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    dataset = build_dataset(args.source_url, args.limit, args.sleep)

    out_path = pathlib.Path(args.output)
    out_path.write_text(json.dumps(dataset, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    resolved = sum(1 for item in dataset if item["lat"] is not None and item["lon"] is not None)
    print(f"\nWrote {len(dataset)} schools to {out_path}")
    print(f"Coordinates resolved for {resolved}/{len(dataset)} schools")


if __name__ == "__main__":
    main()
