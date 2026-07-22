"""Connects briefly to AISStream.io, listens for position reports for a
known, fixed list of ferry vessels (vessels.json), and merges any fresh
positions into latest_positions.json -- keyed by MMSI, with the nearest
known port (ports.json) computed via haversine distance.

Designed to run as a short-lived GitHub Actions job (see
.github/workflows/snapshot.yml), not as a long-running process: it opens
the WebSocket, listens for SNAPSHOT_SECONDS, then closes and exits so the
workflow can commit the result and stop billing Actions minutes.

Note: AISStream's exact message field names are matched defensively
(multiple casings tried) since this was written without a live API key to
test against -- if a real run receives zero messages despite the vessels
being at sea, check https://aisstream.io/documentation for the current
message schema and adjust `_extract_position` accordingly.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from math import asin, cos, radians, sin, sqrt
from pathlib import Path

import websockets

HERE = Path(__file__).resolve().parent
VESSELS_PATH = HERE / "vessels.json"
PORTS_PATH = HERE / "ports.json"
OUTPUT_PATH = HERE / "latest_positions.json"

AISSTREAM_URL = "wss://stream.aisstream.io/v0/stream"
SNAPSHOT_SECONDS = int(os.environ.get("SNAPSHOT_SECONDS", "90"))

# A single bounding box covering the Gulf of Naples, Ischia, Procida and
# Capri -- AISStream requires at least one box in the subscription even
# though FiltersShipMMSI already scopes the result to our known vessels.
BOUNDING_BOX = [[[40.55, 13.70], [40.95, 14.45]]]


def _haversine_meters(lat_a: float, lon_a: float, lat_b: float, lon_b: float) -> float:
    earth_radius = 6_371_000
    phi_a, phi_b = radians(lat_a), radians(lat_b)
    delta_phi = radians(lat_b - lat_a)
    delta_lambda = radians(lon_b - lon_a)
    haversine = sin(delta_phi / 2) ** 2 + cos(phi_a) * cos(phi_b) * sin(delta_lambda / 2) ** 2
    return earth_radius * 2 * asin(sqrt(haversine))


def _nearest_port(latitude: float, longitude: float, ports: list[dict]) -> dict:
    best = min(ports, key=lambda p: _haversine_meters(latitude, longitude, p["latitude"], p["longitude"]))
    distance = _haversine_meters(latitude, longitude, best["latitude"], best["longitude"])
    return {
        "portId": best["id"],
        "distanceMeters": round(distance),
        "atPort": distance <= best["radiusMeters"],
    }


def _extract_position(message: dict) -> dict | None:
    """Returns {mmsi, latitude, longitude, speedKnots} or None if the
    message isn't a position report we can parse."""
    if message.get("MessageType") != "PositionReport":
        return None
    meta = message.get("MetaData", {})
    report = message.get("Message", {}).get("PositionReport", {})
    mmsi = meta.get("MMSI") or report.get("UserID")
    latitude = meta.get("latitude", report.get("Latitude"))
    longitude = meta.get("longitude", report.get("Longitude"))
    if mmsi is None or latitude is None or longitude is None:
        return None
    return {
        "mmsi": str(mmsi),
        "latitude": float(latitude),
        "longitude": float(longitude),
        "speedKnots": report.get("Sog"),
    }


async def collect_positions(api_key: str, mmsi_list: list[str]) -> dict[str, dict]:
    subscription = {
        "APIKey": api_key,
        "BoundingBoxes": BOUNDING_BOX,
        "FilterMessageTypes": ["PositionReport"],
        "FiltersShipMMSI": mmsi_list,
    }
    observed: dict[str, dict] = {}
    try:
        async with websockets.connect(AISSTREAM_URL, open_timeout=15) as socket:
            await socket.send(json.dumps(subscription))
            deadline = asyncio.get_event_loop().time() + SNAPSHOT_SECONDS
            while asyncio.get_event_loop().time() < deadline:
                remaining = deadline - asyncio.get_event_loop().time()
                try:
                    raw = await asyncio.wait_for(socket.recv(), timeout=max(remaining, 0.1))
                except asyncio.TimeoutError:
                    break
                try:
                    message = json.loads(raw)
                except (ValueError, TypeError):
                    continue
                position = _extract_position(message)
                if position is not None:
                    observed[position["mmsi"]] = position
    except (OSError, websockets.exceptions.WebSocketException) as error:
        print(f"AISStream connection failed: {error}", file=sys.stderr)
    return observed


def merge_snapshot(vessels: list[dict], ports: list[dict], observed: dict[str, dict]) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    existing = {}
    if OUTPUT_PATH.exists():
        try:
            previous = json.loads(OUTPUT_PATH.read_text(encoding="utf-8"))
            existing = {entry["mmsi"]: entry for entry in previous.get("vessels", [])}
        except (OSError, ValueError, KeyError):
            existing = {}

    vessels_by_mmsi = {v["mmsi"]: v for v in vessels}
    merged: list[dict] = []
    for mmsi, vessel in vessels_by_mmsi.items():
        position = observed.get(mmsi)
        if position is not None:
            nearest = _nearest_port(position["latitude"], position["longitude"], ports)
            merged.append({
                "mmsi": mmsi,
                "name": vessel["name"],
                "operatorId": vessel["operatorId"],
                "latitude": position["latitude"],
                "longitude": position["longitude"],
                "speedKnots": position["speedKnots"],
                "observedAt": now,
                **nearest,
            })
        elif mmsi in existing:
            merged.append(existing[mmsi])
        # else: never observed yet, omitted until a first position arrives

    return {"generatedAt": now, "vessels": merged}


async def main() -> None:
    api_key = os.environ.get("AISSTREAM_API_KEY")
    if not api_key:
        print("AISSTREAM_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    vessels = json.loads(VESSELS_PATH.read_text(encoding="utf-8"))
    ports = json.loads(PORTS_PATH.read_text(encoding="utf-8"))
    mmsi_list = [v["mmsi"] for v in vessels]

    observed = await collect_positions(api_key, mmsi_list)
    snapshot = merge_snapshot(vessels, ports, observed)

    OUTPUT_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"Observed {len(observed)}/{len(vessels)} vessels this run; wrote {OUTPUT_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
