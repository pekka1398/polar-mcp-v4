# polar-mcp-v4

An MCP server over the Polar AccessLink Dynamic API v4, exposing full
training session data to Claude (or any MCP client): free-text notes, RPE,
feeling, training benefit, cardio/muscle load, per-second heart
rate/distance/speed samples, and GPS route -- everything the v3 AccessLink
API + `.fit`/`.gpx` download pipeline provided, plus the subjective fields
(notes/RPE/feeling) that v3 never exposed.

## Why v4 instead of v3 + file downloads

v3's `/exercises/{id}/fit` and `/gpx` endpoints don't exist in v4. Instead,
`training-sessions/list` with `features=samples,routes,laps,...` returns the
same per-second heart rate/distance/speed arrays and GPS waypoints inline as
JSON, verified field-for-field against a real `.fit` file's non-null
records. One API, one auth flow, no binary file parsing.

## Pieces

- `polar_sync_v4.py` -- pulls sessions from AccessLink v4 day by day (the
  API caps date range at 1 day whenever `features` is used) and writes one
  JSON file per session under `data/v4/sessions/<id>.json`. Handles the
  OAuth refresh-token flow, including persisting a rotated refresh token
  back to `.env`.
- `polar_analysis.py` -- turns the raw per-second arrays into things worth
  reading: three charts (heart rate, speed, GPS route) plus compressed
  summaries (15s-bucketed heart rate, per-km pace splits, heart rate
  peak/valley turning points).
- `polar_mcp_v4.py` -- the MCP server itself (built on
  [FastMCP](https://gofastmcp.com)), read-only: it only reads what
  `polar_sync_v4.py` already wrote to disk, so it starts instantly and
  never touches the network. Tools:
  - `polar_list_sessions` -- basic stats for every archived session
  - `polar_get_session` -- full detail for one session: note, RPE,
    feeling, laps, comments, plus the compressed analysis and three charts
    by default (pass `include_raw_samples=True` for the actual per-second
    arrays)
  - `polar_search_notes` -- substring search over session notes
  - `polar_query_at_time` -- exact heart rate/speed/distance/GPS position
    at one specific second into a session
- `daily_sync.sh` / `service/` -- systemd timer wiring to run
  `polar_sync_v4.py --days 7` daily.

## Setup

1. Register a client at <https://admin.polaraccesslink.com>.
2. Get an authorization code via the v4 OAuth flow
   (`https://auth.polar.com/oauth/authorize`) with the scopes you need
   (`training_sessions:read calendar:read` at minimum), exchange it for a
   refresh token, and put `ClientId`, `ClientSecret`, and
   `PolarV4RefreshToken` in a `.env` file (see `polar_sync_v4.py`'s
   `load_env()` for the expected format -- `export KEY="value"` lines).
3. `uv venv .venv && uv pip install --python .venv/bin/python fastmcp requests matplotlib`
4. `python3 polar_sync_v4.py --days 90` to backfill.
5. Register the server with your MCP client, e.g.:
   `claude mcp add polar-v4 -s user -- /path/to/.venv/bin/python /path/to/polar_mcp_v4.py`

## Notes

- v4 access tokens expire in ~1 hour; `polar_sync_v4.py` refreshes on every
  run and persists a rotated refresh token if Polar issues one.
- `from`/`to` query params must have no timezone suffix
  (`YYYY-MM-DDTHH:mm:ss`) -- this isn't obvious from the docs and appending
  `Z` makes the API reject the request outright.
- `training-sessions/list` allows a 90-day span with no `features`, but
  only 1 day at a time once `features` is used.
