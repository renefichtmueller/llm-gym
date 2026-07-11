#!/bin/zsh
cd "$(dirname "$0")"
# macOS defaults to a 256 open-file soft limit (hard limit is unlimited) — too low
# for a process doing sustained subprocess+HTTP work (training, pipeline verify,
# catalog sync). A leak anywhere hits this ceiling fast (seen live: a polling
# script exhausted it within 2-3 lanes). Raise the soft limit defensively.
ulimit -n 4096 2>/dev/null || true
set -a; source .env 2>/dev/null; set +a
exec .venv/bin/python -m llm_gym
