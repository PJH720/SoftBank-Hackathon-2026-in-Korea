#!/usr/bin/env bash
# Run the local clef-flash decision model for the analyzer's 1-second service pick (app/quick.py).
#   - needs Ollama >= 0.35.1 (the /v1/systemone endpoint). Your own Ollama is never upgraded or restarted:
#     if the one on PATH is older, the official release is downloaded (checksum-verified) into
#     ~/.cache/cloudmorph and runs on its own port with its own model folder.
#   - pulls clef-flash (~12 GB, once), loads it, and prints CLEF_BASE_URL for .env.
# Usage: scripts/clef-up.sh            start (idempotent)
#        scripts/clef-up.sh --down     stop the server this script started
# Env:   CLEF_PORT (11436)  CLEF_MODEL (clef-flash)  CLEF_OLLAMA_VERSION (v0.40.2)
set -euo pipefail
PORT="${CLEF_PORT:-11436}"; MODEL="${CLEF_MODEL:-clef-flash}"; VERSION="${CLEF_OLLAMA_VERSION:-v0.40.2}"
CACHE="${XDG_CACHE_HOME:-$HOME/.cache}/cloudmorph"; mkdir -p "$CACHE/logs"
URL="http://127.0.0.1:$PORT"; PIDFILE="$CACHE/ollama-$PORT.pid"

say() { printf '\033[1;35m[clef]\033[0m %s\n' "$*"; }
die() { printf '\033[1;31m[clef] %s\033[0m\n' "$*" >&2; exit 1; }

if [[ "${1:-}" == "--down" ]]; then
  [[ -f "$PIDFILE" ]] && kill "$(cat "$PIDFILE")" 2>/dev/null && say "stopped ollama on :$PORT"
  rm -f "$PIDFILE"; exit 0
fi

# version a >= b ("0.40.2" >= "0.35.1")
newer() { [[ "$(printf '%s\n%s\n' "$2" "$1" | sort -V | head -n 1)" == "$2" ]]; }
version_of() { "$1" --version 2>/dev/null | grep -oE 'client version is [0-9.]+|version is [0-9.]+' | tail -n 1 | grep -oE '[0-9.]+$' || true; }

# ---- 1. a server on the port already? -----------------------------------------
running="$(curl -sf -m 2 "$URL/api/version" | grep -oE '[0-9]+\.[0-9]+\.[0-9]+' || true)"
if [[ -n "$running" ]]; then
  newer "$running" "0.35.1" || die "port $PORT already runs Ollama $running (< 0.35.1); set CLEF_PORT to a free port"
  say "Ollama $running already on :$PORT"
  OLLAMA="$(command -v ollama || true)"
  [[ -n "$OLLAMA" ]] && newer "$(version_of "$OLLAMA")" "0.35.1" || OLLAMA="$CACHE/ollama-$VERSION/ollama"
else
  # ---- 2. find or fetch an Ollama binary >= 0.35.1 -----------------------------
  OLLAMA="$(command -v ollama || true)"
  if [[ -z "$OLLAMA" ]] || ! newer "$(version_of "$OLLAMA")" "0.35.1"; then
    OLLAMA="$CACHE/ollama-$VERSION/ollama"
    if [[ ! -x "$OLLAMA" ]]; then
      case "$(uname -s)-$(uname -m)" in
        Darwin-*) asset="ollama-darwin.tgz" ;;
        Linux-x86_64) asset="ollama-linux-amd64.tar.zst" ;;
        Linux-aarch64|Linux-arm64) asset="ollama-linux-arm64.tar.zst" ;;
        *) die "unsupported platform $(uname -s)-$(uname -m)" ;;
      esac
      dir="$CACHE/ollama-$VERSION"; mkdir -p "$dir"
      say "downloading Ollama $VERSION ($asset) -> $dir"
      curl -fL --progress-bar -o "$dir/$asset" "https://github.com/ollama/ollama/releases/download/$VERSION/$asset"
      want="$(curl -sf "https://api.github.com/repos/ollama/ollama/releases/tags/$VERSION" \
        | python3 -c "import json,sys; print(next(a['digest'] for a in json.load(sys.stdin)['assets'] if a['name']=='$asset').split(':')[1])" 2>/dev/null || true)"
      got="$(shasum -a 256 "$dir/$asset" | cut -d' ' -f1)"
      [[ -n "$want" ]] || die "could not read the release checksum from GitHub; refusing an unverified binary"
      [[ "$want" == "$got" ]] || { rm -f "$dir/$asset"; die "checksum mismatch for $asset"; }
      say "checksum ok ($got)"
      if [[ "$asset" == *.tgz ]]; then tar -xzf "$dir/$asset" -C "$dir"; else zstd -dc "$dir/$asset" | tar -x -C "$dir"; fi
      [[ -x "$dir/ollama" ]] || OLLAMA="$(find "$dir" -type f -name ollama -perm -u+x | head -n 1)"
    fi
  fi
  # ---- 3. start it on its own port and model folder ---------------------------
  say "starting Ollama on :$PORT (models in $CACHE/ollama-models)"
  OLLAMA_HOST="127.0.0.1:$PORT" OLLAMA_MODELS="$CACHE/ollama-models" OLLAMA_KEEP_ALIVE=-1 OLLAMA_FLASH_ATTENTION=1 \
    nohup "$OLLAMA" serve > "$CACHE/logs/ollama-$PORT.log" 2>&1 &
  echo $! > "$PIDFILE"
  for _ in $(seq 1 30); do curl -sf -m 2 "$URL/api/version" >/dev/null && break; sleep 1; done
  curl -sf -m 2 "$URL/api/version" >/dev/null || die "Ollama did not start, see $CACHE/logs/ollama-$PORT.log"
fi

# ---- 4. model + warm-up (the ~4 s load happens here, not in the first deploy) ---
if ! curl -sf "$URL/api/tags" | grep -q "\"$MODEL"; then
  say "pulling $MODEL (~12 GB, first time only)"
  OLLAMA_HOST="127.0.0.1:$PORT" "$OLLAMA" pull "$MODEL"
fi
body='{"model":"'"$MODEL"'","keep_alive":-1,"state":"warm-up","questions":{"ok":{"type":"noul","instructions":"Is this a warm-up?"}}}'
curl -sf -m 120 "$URL/v1/systemone" -H 'Content-Type: application/json' -d "$body" >/dev/null || die "$MODEL did not answer on $URL/v1/systemone"
say "$MODEL is loaded and answering on $URL"
echo "CLEF_BASE_URL=$URL"
