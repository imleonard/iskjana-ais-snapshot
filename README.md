# ISKJANA AIS snapshot

Periodically checks the position of a known, fixed set of Ischia-area ferry
vessels via [AISStream.io](https://aisstream.io) and publishes the result as
a plain JSON file (`latest_positions.json`) in this repository, refreshed
automatically by a scheduled GitHub Actions workflow.

## Why this exists

The ISKJANA ferry schedule engine (in the private `FerryBackend` repo) knows,
per operator, which vessel names it runs, but not which specific vessel is
serving a given scheduled departure on a given day. This repo answers "where
is vessel X right now" for that small known set, so the schedule engine can
match a vessel to a departure by checking which known vessel is near the
relevant port around departure time.

This deliberately does **not** keep a persistent connection open 24/7. It
connects to AISStream for a short window (default 90 seconds, see
`SNAPSHOT_SECONDS` in `snapshot.py`) every time the scheduled workflow fires,
then disconnects. That keeps it running entirely on GitHub's free Actions
minutes for public repositories (unlimited, no cost, no credit card) instead
of requiring an always-on server.

## Setup

1. Create a free API key at [aisstream.io](https://aisstream.io).
2. In this repository's settings, add it as an Actions secret named
   `AISSTREAM_API_KEY` (Settings -> Secrets and variables -> Actions -> New
   repository secret). It is never written to the code or logs.
3. Make sure this repository is **public** -- Actions minutes are free and
   unlimited only for public repositories. Do not put anything proprietary
   in this repo; it only contains vessel names/MMSIs (already public
   information) and port coordinates.
4. Enable Actions if prompted (Settings -> Actions -> General -> Allow all
   actions).
5. The workflow runs every 15 minutes on its own once merged to `main`. You
   can also trigger it manually from the Actions tab ("Run workflow") to
   test it immediately.

## Consuming the data

`latest_positions.json` is a plain file in a public repo, so it can be read
by anyone (including the FerryBackend service) via the raw GitHub URL, e.g.:

```
https://raw.githubusercontent.com/<your-username>/iskjana-ais-snapshot/main/latest_positions.json
```

No authentication needed to read it. Each entry looks like:

```json
{
  "mmsi": "247061200",
  "name": "CELESTINA",
  "operatorId": "alilauro",
  "latitude": 40.746,
  "longitude": 13.952,
  "speedKnots": 0.1,
  "observedAt": "2026-07-22T05:03:11+00:00",
  "portId": "ischia_porto",
  "distanceMeters": 210,
  "atPort": true
}
```

A vessel is only listed once it has been observed at least once; entries
persist across runs (carrying forward the last known position) until a
fresher one replaces them, so a temporary AIS gap doesn't make a vessel
disappear from the file.

## Maintenance

- **`vessels.json`** is a manually-maintained copy of the relevant entries
  from `FerryBackend/data/vessel_registry.json` (only vessels with a real
  MMSI). If the vessel registry changes there, update this file too.
- **`ports.json`** coordinates were looked up via OpenStreetMap/Nominatim
  and the app's own municipality reference points; the `radiusMeters` per
  port is a starting guess (1200m) -- tighten or loosen it once you see how
  well it distinguishes "at port" from "just passing nearby" in practice.
- **AISStream message format**: `snapshot.py` parses `PositionReport`
  messages defensively, but was written without a live API key to test
  against. If a real run connects successfully but parses zero positions,
  check the current message schema at
  https://aisstream.io/documentation and adjust `_extract_position`.
