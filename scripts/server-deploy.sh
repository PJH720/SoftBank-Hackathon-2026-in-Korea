#!/usr/bin/env bash
# Update the CloudMorph control-plane server (laptop or GitHub Actions: .github/workflows/server-cd.yml).
#   scripts/server-deploy.sh [git-ref]        (default: main; a branch, tag or commit SHA on origin)
# Env: CLOUDMORPH_SERVER=cloudmorph@34.64.253.41  CLOUDMORPH_DOMAIN=cloudmorph.34-64-253-41.sslip.io
# The ref is resolved to one commit on origin; the server checks it out and the dashboard is built from a
# clean checkout of that same commit, so local edits or another checked-out branch never leak into the server.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
REF="${1:-main}"
SERVER="${CLOUDMORPH_SERVER:?set CLOUDMORPH_SERVER=user@host}"
DOMAIN="${CLOUDMORPH_DOMAIN:?set CLOUDMORPH_DOMAIN}"
say() { printf '\033[1;36m[server]\033[0m %s\n' "$*"; }

git -C "$ROOT" fetch -q origin "$REF"
SHA="$(git -C "$ROOT" rev-parse FETCH_HEAD)"
say "code -> $REF ($SHA)"
ssh "$SERVER" "cd ~/cloudmorph && git fetch -q origin && git checkout -q --force '$SHA' && git log --oneline -n 1"
ssh "$SERVER" "cd ~/cloudmorph/backend && ~/.local/bin/uv sync -q"

say "dashboard build"
BUILD="$(mktemp -d)/cloudmorph-build"
trap 'git -C "$ROOT" worktree remove --force "$BUILD" >/dev/null 2>&1 || true' EXIT
git -C "$ROOT" worktree add -q --detach "$BUILD" "$SHA"
(cd "$BUILD/frontend" && npm ci --no-audit --no-fund >/dev/null && npx vite build >/dev/null)
rsync -az --delete "$BUILD/frontend/dist/" "$SERVER:cloudmorph-web/"

say "config"
scp -q "$BUILD/deploy/server/Caddyfile" "$SERVER:Caddyfile"
scp -q "$BUILD/deploy/server/cloudmorph-backend.service" "$SERVER:/tmp/cloudmorph-backend.service"
ssh "$SERVER" "sudo mv /tmp/cloudmorph-backend.service /etc/systemd/system/ && sudo systemctl daemon-reload && sudo systemctl enable -q cloudmorph-backend && sudo systemctl restart cloudmorph-backend"
ssh "$SERVER" "docker rm -f cloudmorph-caddy >/dev/null 2>&1; docker run -d --name cloudmorph-caddy --restart unless-stopped --network host \
  -e CLOUDMORPH_DOMAIN='$DOMAIN' -v \$HOME/Caddyfile:/etc/caddy/Caddyfile:ro -v \$HOME/cloudmorph-web:/srv:ro \
  -v cloudmorph_caddy_data:/data -v cloudmorph_caddy_config:/config caddy:2 >/dev/null"

say "waiting for https://$DOMAIN/health"
for i in $(seq 1 60); do curl -sf "https://$DOMAIN/health" >/dev/null && break; sleep 2; done
curl -sf "https://$DOMAIN/health" >/dev/null && say "up: https://$DOMAIN ($SHA)" || { say "health check failed; journalctl -u cloudmorph-backend"; exit 1; }
