"""Build static geo lookup tables for runtime distance ranking.

Outputs two JSON files used by lib/geo.ts:
  data/geo/us-zip-centroids.json   { "89501": [lat, lng], ... }   (~33K rows)
  data/geo/us-city-centroids.json  { "RENO,NV": [lat, lng], ... } (~10K rows, city-state level)

The ZIP file is the primary lookup. The city file is a fallback for query-side
geocoding when the user types "Reno NV" without a ZIP. Both are derived from
pgeocode's bundled US postal-code database (public-domain).

Usage:
    python scripts/build-geo-data.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import pgeocode  # type: ignore

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / "data" / "geo"
OUT_DIR.mkdir(parents=True, exist_ok=True)


def main() -> int:
    print("Loading US postal-code database via pgeocode...")
    nomi = pgeocode.Nominatim("us")
    df = nomi._data_frame  # full DataFrame; column names in pgeocode 0.5
    # Columns of interest: postal_code, place_name, state_code, latitude, longitude
    df = df.dropna(subset=["postal_code", "latitude", "longitude"])
    print(f"  {len(df):,} rows after dropping rows without coords")

    # ZIP → [lat, lng] (round to 4 decimals: ~11m precision, plenty for our use)
    zip_map: dict[str, list[float]] = {}
    for _, row in df.iterrows():
        zc = str(row["postal_code"]).strip()
        # Only 5-digit US ZIPs
        if not re.fullmatch(r"\d{5}", zc):
            continue
        try:
            lat = round(float(row["latitude"]), 4)
            lng = round(float(row["longitude"]), 4)
        except (TypeError, ValueError):
            continue
        zip_map[zc] = [lat, lng]

    zip_path = OUT_DIR / "us-zip-centroids.json"
    zip_path.write_text(json.dumps(zip_map, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {zip_path} ({zip_path.stat().st_size:,} bytes, {len(zip_map):,} ZIPs)")

    # City+State → [lat, lng] (centroid = mean of all matching ZIPs)
    # Key format: "CITY,ST" uppercase. Some ZIPs have alt place names — use
    # the primary `place_name` column. When multiple ZIPs share the same
    # CITY,ST, average their centroids (ZIPs are roughly uniformly sized
    # within a city, so unweighted mean is fine for ranking purposes).
    from collections import defaultdict
    city_acc: dict[str, list[list[float]]] = defaultdict(list)
    for _, row in df.iterrows():
        city = str(row.get("place_name", "")).strip().upper()
        st = str(row.get("state_code", "")).strip().upper()
        if not city or not st or len(st) != 2:
            continue
        try:
            lat = float(row["latitude"])
            lng = float(row["longitude"])
        except (TypeError, ValueError):
            continue
        city_acc[f"{city},{st}"].append([lat, lng])

    city_map: dict[str, list[float]] = {}
    for k, coords in city_acc.items():
        if not coords:
            continue
        avg_lat = sum(c[0] for c in coords) / len(coords)
        avg_lng = sum(c[1] for c in coords) / len(coords)
        city_map[k] = [round(avg_lat, 4), round(avg_lng, 4)]

    city_path = OUT_DIR / "us-city-centroids.json"
    city_path.write_text(json.dumps(city_map, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {city_path} ({city_path.stat().st_size:,} bytes, {len(city_map):,} city,ST pairs)")

    # State → [lat, lng] (rough centroid of all ZIPs in the state)
    state_acc: dict[str, list[list[float]]] = defaultdict(list)
    for _, row in df.iterrows():
        st = str(row.get("state_code", "")).strip().upper()
        if not st or len(st) != 2:
            continue
        try:
            lat = float(row["latitude"])
            lng = float(row["longitude"])
        except (TypeError, ValueError):
            continue
        state_acc[st].append([lat, lng])

    state_map: dict[str, list[float]] = {}
    for st, coords in state_acc.items():
        if not coords:
            continue
        avg_lat = sum(c[0] for c in coords) / len(coords)
        avg_lng = sum(c[1] for c in coords) / len(coords)
        state_map[st] = [round(avg_lat, 4), round(avg_lng, 4)]

    state_path = OUT_DIR / "us-state-centroids.json"
    state_path.write_text(json.dumps(state_map, separators=(",", ":")), encoding="utf-8")
    print(f"  wrote {state_path} ({state_path.stat().st_size:,} bytes, {len(state_map)} states)")

    # Sanity check: print Reno NV
    print()
    print("Sanity checks:")
    print(f"  ZIP 89511 (Reno NV) → {zip_map.get('89511')}")
    print(f"  ZIP 01201 (Pittsfield MA) → {zip_map.get('01201')}")
    print(f"  CITY 'RENO,NV' → {city_map.get('RENO,NV')}")
    print(f"  CITY 'PITTSFIELD,MA' → {city_map.get('PITTSFIELD,MA')}")
    print(f"  STATE 'NV' → {state_map.get('NV')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
