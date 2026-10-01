#!/bin/sh
set -eu

REPO_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN="$REPO_ROOT/cli/.venv/bin/python"
fi
if [ ! -x "$PYTHON_BIN" ]; then
  PYTHON_BIN=python3
fi

PYTHONPATH="$REPO_ROOT/cli/src:$REPO_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
  exec "$PYTHON_BIN" -m backend.server "$@"
