#!/usr/bin/env python3
"""
polar_sync_v4.py -- primary sync: pulls complete training session data from
AccessLink Dynamic API v4 (notes, RPE, feeling, training load, per-second
heart rate/distance/speed samples, GPS route, laps, comments -- everything
verified to match what the old v3 + .fit/.gpx download pipeline provided).

v3 (legacy/) is kept only as a frozen historical archive; this script is now
the only thing that needs to run day to day.

Storage: one JSON file per session, keyed by v4's own session id --
    data/v4/sessions/<session-id>.json
Always re-fetched and overwritten for the requested window (notes/RPE can be
edited after the fact, so re-pulling is never wasted).

v4-specific quirks discovered by trial + reading the real docs:
- from/to must be "YYYY-MM-DDTHH:mm:ss", no timezone suffix.
- Without `features`, from..to can span up to 90 days. WITH `features`
  (needed for basically everything useful here), only 1 day at a time --
  so the requested window is always walked one calendar day at a time.
- Access tokens expire in ~1 hour; refresh_token mints a new one every run,
  and Polar may rotate the refresh_token itself, which is persisted back to
  .env immediately so a stale one is never left behind.
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

HERE = Path(__file__).resolve().parent
ENV_PATH = HERE / ".env"
SESSIONS_DIR = HERE / "data" / "v4" / "sessions"
LOG_PATH = HERE / "data" / "v4_sync.log"

TOKEN_URL = "https://auth.polar.com/oauth/token"
BASE = "https://www.polaraccesslink.com/v4/data"
TIMEOUT = 60

# Every feature that gives us data equivalent to (or beyond) what the old
# v3 + .fit/.gpx pipeline had, plus what only v4 has (note/RPE/feeling).
FEATURES = [
    "samples", "routes", "laps", "training-load-report", "comments",
    "hill-splits", "statistics", "zones", "pause-times",
    "strength-training-results", "test-results", "physical-info",
]


def log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} {msg}"
    print(line)
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_env() -> dict:
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            line = line.removeprefix("export ")
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip().strip('"').strip("'")
    return env


def save_refresh_token(new_refresh_token: str) -> None:
    """Overwrite PolarV4RefreshToken in-place if Polar rotated it on refresh."""
    lines = ENV_PATH.read_text().splitlines(keepends=True)
    out = []
    replaced = False
    for line in lines:
        if line.strip().startswith("export PolarV4RefreshToken="):
            out.append(f'export PolarV4RefreshToken="{new_refresh_token}"\n')
            replaced = True
        else:
            out.append(line)
    if not replaced:
        out.append(f'export PolarV4RefreshToken="{new_refresh_token}"\n')
    ENV_PATH.write_text("".join(out))
    os.chmod(ENV_PATH, 0o600)


def refresh_access_token(env: dict) -> str:
    """Exchange the stored refresh_token for a fresh v4 access token."""
    import base64

    credentials = base64.b64encode(
        f"{env['ClientId']}:{env['ClientSecret']}".encode()
    ).decode()
    resp = requests.post(
        TOKEN_URL,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
        data={
            "grant_type": "refresh_token",
            "refresh_token": env["PolarV4RefreshToken"],
        },
        timeout=TIMEOUT,
    )
    resp.raise_for_status()
    token_data = resp.json()

    new_refresh = token_data.get("refresh_token")
    if new_refresh and new_refresh != env["PolarV4RefreshToken"]:
        save_refresh_token(new_refresh)
        log("Refresh token rotated; .env updated.")

    return token_data["access_token"]


def fmt_v4(dt: datetime) -> str:
    """v4 wants naive local-time ISO strings with no timezone suffix."""
    return dt.strftime("%Y-%m-%dT%H:%M:%S")


def fetch_day(access_token: str, day: datetime) -> list:
    """One calendar day of training sessions, with every feature we use."""
    params = {
        "from": fmt_v4(day),
        "to": fmt_v4(day + timedelta(days=1)),
        "features": FEATURES,
    }
    resp = requests.get(
        f"{BASE}/training-sessions/list",
        headers={
            "Authorization": f"Bearer {access_token}",
            "Accept": "application/json",
        },
        params=params,
        timeout=TIMEOUT,
    )
    if resp.status_code == 400:
        log(f"  400 for {day.date()}: {resp.text}")
        return []
    resp.raise_for_status()
    return resp.json().get("trainingSessions", [])


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--days", type=int, default=90,
        help="How many days back from today to sync (default: 90, the v4 max).",
    )
    args = ap.parse_args()

    env = load_env()
    for required in ("ClientId", "ClientSecret", "PolarV4RefreshToken"):
        if required not in env:
            log(f"FAIL: missing {required} in .env. Run the v4 auth flow first.")
            return 1

    access_token = refresh_access_token(env)
    log("Got fresh v4 access token.")

    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)

    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    total_sessions = 0
    written = 0

    for offset in range(args.days, -1, -1):
        day = today - timedelta(days=offset)
        sessions = fetch_day(access_token, day)
        for session in sessions:
            sid = session.get("identifier", {}).get("id")
            if not sid:
                continue
            session["fetched_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
            out_path = SESSIONS_DIR / f"{sid}.json"
            out_path.write_text(
                json.dumps(session, indent=2, ensure_ascii=False), encoding="utf-8"
            )
            written += 1
            note = "yes" if session.get("note") else "no"
            log(f"  {session.get('startTime')}: wrote {sid} (note={note})")
        total_sessions += len(sessions)

    log(f"Done. {total_sessions} sessions seen, {written} written to {SESSIONS_DIR}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
