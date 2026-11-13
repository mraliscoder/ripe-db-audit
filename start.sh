#!/bin/sh
set -eu
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
if ! .venv/bin/python -c 'import fastapi, uvicorn, httpx' >/dev/null 2>&1; then
  .venv/bin/python -m pip install -r requirements.lock
fi
if [ ! -d frontend/node_modules ]; then
  npm --prefix frontend ci
fi
npm --prefix frontend run build
exec .venv/bin/python -m uvicorn app.main:app --app-dir backend --host "${AUDIT_HOST:-127.0.0.1}" --port "${PORT:-8090}"
