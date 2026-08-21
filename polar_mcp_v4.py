#!/usr/bin/env python3
"""
polar_mcp_v4.py -- MCP server over the full v4 session archive that
polar_sync_v4.py maintains under data/v4/sessions/<id>.json. Each file has
everything: note, RPE, feeling, training load, per-second heart
rate/distance/speed samples, GPS route, laps, comments.

Deliberately read-only: fetching from Polar is polar_sync_v4.py's job (run
by hand or via daily_sync.sh / the systemd timer). This server only reads
what's already on disk.

Run:
    .venv/bin/python polar_mcp_v4.py          # stdio, for Claude Desktop/Code
"""
import json
from pathlib import Path
from typing import Optional

from fastmcp import FastMCP
from fastmcp.utilities.types import Image

import polar_analysis

HERE = Path(__file__).resolve().parent
SESSIONS_DIR = HERE / "data" / "v4" / "sessions"

app = FastMCP("Polar Training Sessions (v4)")


def _load_json(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def _iter_sessions():
    if not SESSIONS_DIR.exists():
        return
    for f in sorted(SESSIONS_DIR.glob("*.json")):
        session = _load_json(f)
        if session:
            yield session


def _basics(session: dict) -> dict:
    tlr = session.get("trainingLoadReport", {})
    return {
        "id": session.get("identifier", {}).get("id"),
        "start_time": session.get("startTime"),
        "stop_time": session.get("stopTime"),
        "sport_id": session.get("sport", {}).get("id"),
        "duration_min": round((session.get("durationMillis") or 0) / 60000, 1),
        "distance_m": session.get("distanceMeters"),
        "calories": session.get("calories"),
        "hr_avg": session.get("hrAvg"),
        "hr_max": session.get("hrMax"),
        "has_note": bool(session.get("note")),
        "feeling": session.get("feeling"),
        "session_rpe": tlr.get("sessionRpe"),
        "training_benefit": session.get("trainingBenefit"),
        "cardio_load": tlr.get("cardioLoad"),
    }


@app.tool()
def polar_list_sessions(start_date: Optional[str] = None, end_date: Optional[str] = None) -> list[dict]:
    """List all archived training sessions with their basic stats, most
    recent first. Not just annotated ones -- every synced session.

    Args:
        start_date: 'YYYY-MM-DD', inclusive. Optional.
        end_date:   'YYYY-MM-DD', inclusive. Optional.
    """
    results = []
    for session in _iter_sessions():
        b = _basics(session)
        if not b["start_time"]:
            continue
        session_date = b["start_time"][:10]
        if start_date and session_date < start_date:
            continue
        if end_date and session_date > end_date:
            continue
        results.append(b)
    results.sort(key=lambda r: r["start_time"] or "", reverse=True)
    return results


@app.tool()
def polar_get_session(session_id: str, include_raw_samples: bool = False):
    """Get full detail for one archived training session: note, RPE,
    feeling, training benefit, cardio/muscle load, laps, comments -- plus,
    by default, three charts (heart rate, speed, GPS route) and compressed
    time-series summaries (15s-bucketed heart rate, per-km pace splits,
    heart rate peak/valley turning points). This replaces reading the raw
    per-second arrays for basically every real question ("how did my pace
    hold up", "where did my heart rate spike").

    Only pass include_raw_samples=True if you need the actual per-second
    arrays themselves (e.g. to compute something the built-in analysis
    doesn't cover) -- for a single specific instant, prefer
    polar_query_at_time instead of pulling the whole array.

    Args:
        session_id: the v4 session id, from polar_list_sessions.
        include_raw_samples: include the raw exercises[].samples and
            exercises[].routes arrays (large, ~1 point/sec). Default False.
    """
    path = SESSIONS_DIR / f"{session_id}.json"
    session = _load_json(path)
    if session is None:
        return {"error": f"No session archived for id '{session_id}'."}

    session = json.loads(json.dumps(session))  # working copy
    exercises = session.get("exercises", [])
    ex = exercises[0] if exercises else {}

    if include_raw_samples:
        return session

    had_timeseries = polar_analysis.has_timeseries(ex)
    analysis = polar_analysis.analyze(ex) if had_timeseries else {}
    chart_paths = polar_analysis.generate_charts(session_id, ex) if had_timeseries else {}

    for e in exercises:
        e.pop("samples", None)
        e.pop("routes", None)
    session["analysis"] = analysis

    if not had_timeseries:
        return session
    result = [session]
    for label in ("hr", "speed", "gps"):
        if label in chart_paths:
            result.append(Image(path=chart_paths[label]))
    return result


@app.tool()
def polar_search_notes(query: str) -> list[dict]:
    """Search archived session notes for a substring (case-insensitive).
    Useful for finding past sessions by what you wrote about them, e.g.
    "which runs did I mention knee pain in".

    Args:
        query: text to search for within note contents.
    """
    query_lower = query.lower()
    results = []
    for session in _iter_sessions():
        note = session.get("note")
        if not note or query_lower not in note.lower():
            continue
        idx = note.lower().index(query_lower)
        snippet_start = max(0, idx - 40)
        snippet = note[snippet_start:idx + len(query) + 40]
        results.append({
            **_basics(session),
            "snippet": ("..." if snippet_start > 0 else "") + snippet,
        })
    results.sort(key=lambda r: r.get("start_time") or "", reverse=True)
    return results


@app.tool()
def polar_query_at_time(session_id: str, seconds: int) -> dict:
    """Get the exact heart rate, speed, cumulative distance, and GPS
    position at one specific second into a session -- a precise lookup,
    not a summary. Use this instead of pulling the full samples/route
    arrays when you only need a handful of specific instants (e.g. "what
    was my heart rate at 12:30 into the run", or several such points to
    describe a spike).

    Args:
        session_id: the v4 session id.
        seconds: elapsed seconds from the start of the exercise (0-based).
    """
    path = SESSIONS_DIR / f"{session_id}.json"
    session = _load_json(path)
    if session is None:
        return {"error": f"No session archived for id '{session_id}'."}
    if not session.get("exercises"):
        return {"error": "Session has no exercises."}

    ex = session["exercises"][0]
    samples = {s["type"]: s["values"] for s in ex.get("samples", {}).get("samples", [])}
    wp = ex.get("routes", {}).get("route", {}).get("wayPoints", [])

    def at(arr, i):
        return arr[i] if arr and 0 <= i < len(arr) else None

    duration_s = (ex.get("durationMillis") or 0) // 1000
    if seconds < 0 or seconds > duration_s:
        return {"error": f"seconds must be within [0, {duration_s}] for this session."}

    point = wp[seconds] if wp and 0 <= seconds < len(wp) else None
    return {
        "seconds": seconds,
        "heart_rate": at(samples.get("HEART_RATE"), seconds),
        "speed_kmh": at(samples.get("SPEED"), seconds),
        "distance_m": at(samples.get("DISTANCE"), seconds),
        "latitude": point["latitude"] if point else None,
        "longitude": point["longitude"] if point else None,
    }


def main():
    app.run()


if __name__ == "__main__":
    main()
