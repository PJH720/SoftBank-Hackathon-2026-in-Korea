#!/usr/bin/env bash
# Lock down a node-pool VM (GCP / AWS / OCI / any Linux box with Docker) before it runs user apps.
#   deploy/nodes/harden-node.sh <user>@<host> [ssh key]
#
# 1. Containers cannot reach the cloud metadata server (169.254.169.254 on GCP, AWS and OCI alike), so an app
#    cannot borrow the VM's identity: same unit as the control server (deploy/server/cloudmorph-egress-guard.service).
# 2. Every app gets its own Docker network (nodes.app_network), and Docker does not route between bridge
#    networks: an app cannot reach another app's database container even with its password.
#    Nothing to install for that; this script only removes the legacy shared network once nothing uses it.
set -euo pipefail
TARGET="${1:?usage: harden-node.sh <user>@<host> [ssh key]}"
KEY="${2:-$HOME/.ssh/id_ed25519}"
HERE="$(cd "$(dirname "$0")" && pwd)"
SSH=(ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10 -i "$KEY" "$TARGET")

"${SSH[@]}" "sudo tee /etc/systemd/system/cloudmorph-egress-guard.service >/dev/null" \
  < "$HERE/../server/cloudmorph-egress-guard.service"
"${SSH[@]}" 'set -e
sudo systemctl daemon-reload
sudo systemctl enable --now cloudmorph-egress-guard.service >/dev/null
sudo iptables -C DOCKER-USER -d 169.254.169.254/32 -j REJECT
# legacy shared network: drop it when no container is attached any more (fails harmlessly otherwise)
docker network rm cloudmorph >/dev/null 2>&1 || true
echo "metadata blocked for containers; networks: $(docker network ls --format "{{.Name}}" | grep -c "^cm-") per-app"'

# check from inside a throwaway container: the metadata server must refuse
if "${SSH[@]}" 'docker run --rm busybox wget -q -T 3 -O- http://169.254.169.254/ >/dev/null 2>&1'; then
  echo "FAILED: a container on $TARGET can still reach the metadata server" >&2
  exit 1
fi
echo "ok: $TARGET"
