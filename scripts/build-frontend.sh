#!/usr/bin/env bash
# Build the frontend bundle into frontend/dist using a throwaway Node
# container (the lab host doesn't need Node installed).
set -euo pipefail
cd "$(dirname "$0")/../frontend"
docker run --rm -u "$(id -u):$(id -g)" -e npm_config_cache=/tmp/.npm -v "$PWD:/app" -w /app node:22-alpine \
  sh -c "npm ci --no-audit --no-fund --loglevel=error && npx tsc --noEmit && npx vite build"
echo "Built $(pwd)/dist"
