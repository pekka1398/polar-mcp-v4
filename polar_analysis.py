"""
polar_analysis.py -- compress a session's per-second samples/route into
things worth reading: three chart images (HR, speed, GPS) plus a few
structured summaries (time-bucketed HR, per-km pace splits, HR turning
points). Charts are cached to disk since they're deterministic given the
session file.
"""
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

HERE = Path(__file__).resolve().parent
CHARTS_DIR = HERE / "data" / "v4" / "charts"


def _extract(ex: dict):
    samples = {s["type"]: s["values"] for s in ex.get("samples", {}).get("samples", [])}
    wp = ex.get("routes", {}).get("route", {}).get("wayPoints", [])
    return samples, wp


def has_timeseries(ex: dict) -> bool:
    samples, wp = _extract(ex)
    return bool(samples.get("HEART_RATE")) or bool(wp)


def downsample(values: list, bucket_sec: int = 15) -> list[dict]:
    rows = []
    for i in range(0, len(values), bucket_sec):
        chunk = [v for v in values[i:i + bucket_sec] if v is not None]
        if not chunk:
            continue
        rows.append({
            "t_start_s": i,
            "avg": round(sum(chunk) / len(chunk), 1),
            "min": round(min(chunk), 1),
            "max": round(max(chunk), 1),
        })
    return rows


def pace_splits(dist_values: list) -> list[dict]:
    splits = []
    last_km, last_t = 0, 0
    for t, d in enumerate(dist_values):
        if d is None:
            continue
        km = int(d // 1000)
        if km > last_km:
            split_s = t - last_t
            splits.append({
                "km": km,
                "split_s": split_s,
                "pace_min_per_km": round(split_s / 60, 2),
            })
            last_km, last_t = km, t
    if dist_values:
        total_d = dist_values[-1] or 0
        final_partial = total_d - last_km * 1000
        if final_partial > 10:
            splits.append({
                "km": f"{last_km}-end",
                "partial_m": round(final_partial),
                "split_s": len(dist_values) - last_t,
            })
    return splits


def turning_points(values: list, min_prominence: float = 8, min_gap: int = 30) -> list[dict]:
    points = []
    n = len(values)
    for i in range(1, n - 1):
        if values[i] is None:
            continue
        is_max = values[i] >= values[i - 1] and values[i] >= values[i + 1]
        is_min = values[i] <= values[i - 1] and values[i] <= values[i + 1]
        if is_max or is_min:
            points.append((i, values[i], "peak" if is_max else "valley"))

    filtered = []
    for p in points:
        if not filtered:
            filtered.append(p)
            continue
        last = filtered[-1]
        if p[0] - last[0] < min_gap:
            if p[2] == last[2] and (
                (p[2] == "peak" and p[1] > last[1]) or (p[2] == "valley" and p[1] < last[1])
            ):
                filtered[-1] = p
            continue
        if abs(p[1] - last[1]) < min_prominence:
            continue
        filtered.append(p)

    return [{"t_s": t, "value": v, "kind": kind} for t, v, kind in filtered]


def analyze(ex: dict) -> dict:
    samples, wp = _extract(ex)
    hr = samples.get("HEART_RATE", [])
    dist = samples.get("DISTANCE", [])
    out = {}
    if hr:
        out["hr_15s_buckets"] = downsample(hr, 15)
        out["hr_turning_points"] = turning_points(hr)
    if dist:
        out["pace_splits_per_km"] = pace_splits(dist)
    if wp:
        lats = [p["latitude"] for p in wp]
        lons = [p["longitude"] for p in wp]
        out["gps_bounding_box"] = {
            "lat_min": min(lats), "lat_max": max(lats),
            "lon_min": min(lons), "lon_max": max(lons),
        }
    return out


def generate_charts(session_id: str, ex: dict, force: bool = False) -> dict:
    """Returns {"hr": path, "speed": path, "gps": path} for whichever
    charts have data, generating + caching to disk on first call."""
    samples, wp = _extract(ex)
    CHARTS_DIR.mkdir(parents=True, exist_ok=True)
    paths = {}

    hr = samples.get("HEART_RATE")
    if hr:
        p = CHARTS_DIR / f"{session_id}_hr.png"
        if force or not p.exists():
            plt.figure(figsize=(10, 3))
            plt.plot(range(len(hr)), hr, linewidth=0.8, color="crimson")
            plt.title("Heart rate (bpm) over time (s)")
            plt.xlabel("seconds"); plt.ylabel("bpm")
            plt.tight_layout()
            plt.savefig(p, dpi=130)
            plt.close()
        paths["hr"] = str(p)

    speed = samples.get("SPEED")
    if speed:
        p = CHARTS_DIR / f"{session_id}_speed.png"
        if force or not p.exists():
            plt.figure(figsize=(10, 3))
            plt.plot(range(len(speed)), speed, linewidth=0.8, color="steelblue")
            plt.title("Speed (km/h) over time (s)")
            plt.xlabel("seconds"); plt.ylabel("km/h")
            plt.tight_layout()
            plt.savefig(p, dpi=130)
            plt.close()
        paths["speed"] = str(p)

    if wp:
        p = CHARTS_DIR / f"{session_id}_gps.png"
        if force or not p.exists():
            lons = [pt["longitude"] for pt in wp]
            lats = [pt["latitude"] for pt in wp]
            plt.figure(figsize=(6, 6))
            plt.plot(lons, lats, linewidth=1.2, color="darkgreen")
            plt.scatter([lons[0]], [lats[0]], color="green", s=40, label="start", zorder=5)
            plt.scatter([lons[-1]], [lats[-1]], color="red", s=40, label="end", zorder=5)
            plt.title("GPS route")
            plt.xlabel("longitude"); plt.ylabel("latitude")
            plt.legend()
            plt.axis("equal")
            plt.tight_layout()
            plt.savefig(p, dpi=130)
            plt.close()
        paths["gps"] = str(p)

    return paths
