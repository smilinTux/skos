#!/usr/bin/env bash
# Build the SKOS Autopilot sandbox images: the egress proxy sidecar plus one image
# per harness (claude-code, pi, opencode). Each image is the confined execution
# environment a harness runs inside (see docs/skos-autopilot-architecture.md §12
# and docs/skos-autopilot-SOP.md §13). Images are tagged locally so a node can run
# confined live execution offline; a node missing them fails closed (no unconfined
# run is ever attempted).
#
# Usage:
#   docker/sandbox/build.sh                 # build all four images
#   docker/sandbox/build.sh proxy           # build a single image (proxy|claude|pi|opencode)
#   docker/sandbox/build.sh pi opencode     # build a subset
#   docker/sandbox/build.sh --no-cache      # force a clean rebuild of all four
#   docker/sandbox/build.sh -h | --help
#
# All four build with plain `docker build` from the repo root (the proxy image
# COPYs src/skos/autopilot/sandbox_proxy.py). No `--network host` or registry
# egress is needed: opencode bundles its openai-compatible provider, pi and claude
# install their CLIs at build time. Next step after a build is the confinement
# proof: RUN_SANDBOX_IT=1 python -m pytest tests/test_sandbox_confinement_it.py
set -euo pipefail

cd "$(dirname "$0")/../.."          # repo root (build context)
D=docker/sandbox
TAG=1                                # image tag: sandbox-<name>:$TAG

# each entry: <name> <dockerfile-subdir> <in-image verify command | ->
#   the verify command runs in the freshly built image and prints the baked CLI
#   version, proving the harness binary is actually present (- = no CLI to check)
HARNESSES=(
  "proxy    proxy     -"
  "claude   claude    claude --version"
  "pi       pi        pi --version"
  "opencode opencode  opencode --version"
)

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }

BUILD_ARGS=()
WANT=()
for a in "$@"; do
  case "$a" in
    -h|--help) usage 0 ;;
    --no-cache|--pull) BUILD_ARGS+=("$a") ;;
    proxy|claude|pi|opencode) WANT+=("$a") ;;
    *) echo "unknown arg: $a" >&2; usage 1 ;;
  esac
done

wanted() {                          # is $1 in the requested set (empty set = all)?
  [ ${#WANT[@]} -eq 0 ] && return 0
  local w; for w in "${WANT[@]}"; do [ "$w" = "$1" ] && return 0; done
  return 1
}

command -v docker >/dev/null 2>&1 || { echo "docker not found on PATH" >&2; exit 127; }

declare -A RESULT
for row in "${HARNESSES[@]}"; do
  read -r name dir verify <<<"$row"
  wanted "$name" || continue
  img="sandbox-${name}:${TAG}"
  echo "== building ${img} =="
  if docker build "${BUILD_ARGS[@]}" -f "$D/$dir/Dockerfile" -t "$img" .; then
    RESULT[$name]=ok
    if [ "$verify" != "-" ]; then
      ver="$(docker run --rm "$img" sh -c "$verify" 2>/dev/null | head -1 || true)"
      echo "   ${name} CLI: ${ver:-<version check failed>}"
    fi
  else
    RESULT[$name]=FAIL
    echo "   ${img} build FAILED" >&2
  fi
done

echo "== images =="
docker image ls --format '{{.Repository}}:{{.Tag}}  {{.Size}}' \
  | grep -E 'sandbox-(proxy|claude|pi|opencode):'"$TAG" || true

echo "== result =="
fail=0
for row in "${HARNESSES[@]}"; do
  read -r name _ _ <<<"$row"
  wanted "$name" || continue
  r="${RESULT[$name]:-skipped}"
  printf '   %-9s %s\n' "$name" "$r"
  [ "$r" = "FAIL" ] && fail=1
done

if [ "$fail" -eq 0 ]; then
  echo "== all requested images built. next: confinement proof =="
  echo "   RUN_SANDBOX_IT=1 python -m pytest tests/test_sandbox_confinement_it.py -q"
fi
exit "$fail"
