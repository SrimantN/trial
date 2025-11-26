#!/usr/bin/env bash
set -e
# This script installs deps, builds frontend, seeds DB, and starts uvicorn on $PORT (Replit)
# When running locally, you can set PORT or default to 8000

# ensure we run from repo root
cd "$(dirname "$0")"

# 1) Install python deps
cd backend/app
python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

# 2) Seed providers DB (create or update)
python seed_providers.py

# 3) Build frontend (install node deps and build)
cd ../../frontend
# Install node modules (Replit provides node/npm)
if [ ! -d "node_modules" ]; then
  npm install --legacy-peer-deps
fi
npm run build

# 4) Move built frontend into backend dist path (backend will serve it)
# Build output is 'dist' (Vite). Ensure backend can find dist at backend/../frontend/dist
cd ..

# 5) Start backend with uvicorn; Replit provides $PORT — default to 8000 if not set
PORT="${PORT:-8000}"
echo "Starting uvicorn on port $PORT"
cd backend/app
# Expose host 0.0.0.0
exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
