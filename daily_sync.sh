#!/bin/bash
# Pull the last 7 days of training sessions from AccessLink v4 -- covers any
# session synced late plus notes/RPE edited after the fact. Safe to run
# daily and safe to run twice: always re-fetches and overwrites.
set -e
cd "$(dirname "$0")"

.venv/bin/python polar_sync_v4.py --days 7
