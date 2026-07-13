#!/usr/bin/env bash
# Build the autopilot sandbox images. Run from anywhere; builds from repo root so
# the proxy Dockerfile can COPY src/skos/autopilot/sandbox_proxy.py.
#
# The proxy + claude images are the minimum to run the confinement proof for the
# default harness. The pi/opencode images have unconfirmed install lines (see
# their Dockerfiles): confirm the CLI install command and validate the real
# output shape before enabling those harnesses live.
set -euo pipefail
cd "$(dirname "$0")/../.."          # repo root
D=docker/sandbox

echo "== building sandbox-proxy:1 =="
docker build -f "$D/proxy/Dockerfile"    -t sandbox-proxy:1    .

echo "== building sandbox-claude:1 (default harness) =="
docker build -f "$D/claude/Dockerfile"   -t sandbox-claude:1   .

echo "== building sandbox-pi:1 (confirm pi install) =="
docker build -f "$D/pi/Dockerfile"       -t sandbox-pi:1       . || echo "pi image build FAILED (confirm install line)"

echo "== building sandbox-opencode:1 (confirm opencode install) =="
docker build -f "$D/opencode/Dockerfile" -t sandbox-opencode:1 . || echo "opencode image build FAILED (confirm install line)"

echo "== images =="
docker image ls --format '{{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E 'sandbox-(proxy|claude|pi|opencode)' || true
