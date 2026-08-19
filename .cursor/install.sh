#!/usr/bin/env bash
# Idempotent repository bootstrap for Epiphany Studio Cloud Agents.
# Prepares the FastAPI backend (Python venv + Alembic schema) and the Vite
# frontend (npm dependencies). Safe to run repeatedly and against cached state.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# The default base image ships Python 3.12 without the venv/ensurepip module.
if ! python3 -m ensurepip --version >/dev/null 2>&1; then
  sudo apt-get update -qq
  sudo apt-get install -y -qq python3.12-venv
fi

# Backend: virtual environment, editable install with dev extras, and the
# Alembic-managed SQLite schema (the source of truth per AGENTS.md).
cd "$REPO_ROOT/backend"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -e ".[dev]"
mkdir -p data
alembic upgrade head
deactivate

# Frontend: install pinned npm dependencies strictly from the committed
# lockfile. `npm ci` is reproducible and does not rewrite package-lock.json.
cd "$REPO_ROOT/frontend"
npm ci
