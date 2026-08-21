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

This is a local stdio MCP server, not a hosted one -- there's no
`/mcp`-style automatic browser OAuth like remote MCP servers (e.g.
TickTick's) offer. Auth is a one-time manual OAuth exchange whose result
(a refresh token) gets saved to `.env`; after that, `polar_sync_v4.py`
refreshes it automatically forever, so you only do this once.

### 1. Register a client

Go to <https://admin.polaraccesslink.com>, log in with your Polar Flow
account, and create a client. Fill in any name/description. For "redirect
URL", use `http://localhost:8080/callback` (it doesn't need to actually be
running anything -- see step 3). Note down the **Client ID** and **Client
Secret** it gives you.

### 2. Get an authorization code

Decide which scopes you need. At minimum `training_sessions:read
calendar:read`; for the full feature set this repo uses, all of:

```
training_sessions:read calendar:read continuous_samples:read activity:read
sleep:read routes:read user_devices:read sports:read training_target:read
nightly_recharge:read skin_contact:read temperature:read tests:read
account_data:read
```

(Some of those may come back rejected/unused depending on your account --
that's fine, request them anyway and see what you actually get back in the
token response's `scope` field.)

Build this URL, filling in your own `client_id` and URL-encoded scope list,
and open it in a browser:

```
https://auth.polar.com/oauth/authorize?response_type=code&client_id=YOUR_CLIENT_ID&redirect_uri=http%3A%2F%2Flocalhost%3A8080%2Fcallback&scope=YOUR_SPACE_DELIMITED_SCOPES_URL_ENCODED
```

Log in and approve. You'll get redirected to
`http://localhost:8080/callback?code=XXXXXX` -- the page itself will fail
to load (nothing is listening on `localhost:8080`, and that's fine), you
only need the `code=` value from the URL bar.

### 3. Exchange the code for tokens

```bash
CLIENT_ID="..."
CLIENT_SECRET="..."
CODE="XXXXXX"   # from the redirect URL above

CREDENTIALS=$(echo -n "${CLIENT_ID}:${CLIENT_SECRET}" | base64 -w0)

curl -X POST https://auth.polar.com/oauth/token \
  -H "Authorization: Basic ${CREDENTIALS}" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -H "Accept: application/json" \
  -d "grant_type=authorization_code&code=${CODE}&redirect_uri=http://localhost:8080/callback"
```

The response is JSON with `access_token`, `refresh_token`, and `scope`.
You only need `refresh_token` -- `polar_sync_v4.py` mints its own access
tokens from it on every run (they expire in ~1 hour; the refresh token
lasts much longer and gets auto-rotated/persisted as needed).

### 4. Write `.env`

In the repo root, create `.env` (and `chmod 600` it -- it holds secrets):

```bash
export ClientId='YOUR_CLIENT_ID'
export ClientSecret='YOUR_CLIENT_SECRET'
export PolarV4RefreshToken="THE_REFRESH_TOKEN_FROM_STEP_3"
```

### 5. Install dependencies and backfill

```bash
uv venv .venv
uv pip install --python .venv/bin/python fastmcp requests matplotlib
python3 polar_sync_v4.py --days 90   # 90 is the v4 max in one run
```

Check `data/v4_sync.log` and `data/v4/sessions/` -- you should see one
JSON file per training session.

### 6. Register the MCP server

For Claude Code, user-scoped (works from any directory, not just this
one):

```bash
claude mcp add polar-v4 -s user -- /path/to/repo/.venv/bin/python /path/to/repo/polar_mcp_v4.py
```

Start a new Claude Code session and run `/mcp` -- you should see `polar-v4`
listed as connected with 4 tools. For other MCP clients, point them at the
same command (`/path/to/.venv/bin/python /path/to/polar_mcp_v4.py`) via
stdio the way you'd add any local MCP server.

### 7. Keep it fresh

Wire up `service/` (systemd timer, runs `daily_sync.sh` -> `polar_sync_v4.py
--days 7` once a day) or just re-run `python3 polar_sync_v4.py --days 7`
by hand whenever you want new sessions / edited notes pulled in.

## Notes

- v4 access tokens expire in ~1 hour; `polar_sync_v4.py` refreshes on every
  run and persists a rotated refresh token if Polar issues one.
- `from`/`to` query params must have no timezone suffix
  (`YYYY-MM-DDTHH:mm:ss`) -- this isn't obvious from the docs and appending
  `Z` makes the API reject the request outright.
- `training-sessions/list` allows a 90-day span with no `features`, but
  only 1 day at a time once `features` is used.
