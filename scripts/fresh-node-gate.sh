#!/usr/bin/env bash
# The skbrain fresh-node E2E gate (card d17892ae, epic fb3cc09d, blocks f3cb6231).
#
# Single documented command:
#
#   scripts/fresh-node-gate.sh
#
# What it does:
#   1. Stages a throwaway copy of THIS skos worktree and of the skmemory repo
#      checkout (default ~/clawd/skcapstone-repos/skmemory, override with
#      SKMEMORY_REPO) into a scratch build context -- never mutates either repo.
#   2. Builds a "clean node" image (docker/fresh-node/) from that staged copy:
#      a brand-new container filesystem, fresh $HOME, no ~/.skcapstone,
#      ~/.skenv, ~/.config/skcapstone, or systemd units -- because none of
#      those paths exist anywhere in this container's history.
#   3. Boots a SEPARATE, ephemeral skmem-pg container (same image tag this
#      host already runs production skmem-pg from, default
#      smilintux/skmem-pg:pg17-bm25-age, override with SKMEM_PG_IMAGE) with a
#      brand-new, empty data volume, so its own first-boot init
#      (deploy/skmem-pg/initdb/00-run-init.sh) applies schema.sql + every
#      forward migration for real. This is NOT the host's real `skmem-pg`
#      container -- different container, different network, different data,
#      --rm'd at the end.
#   4. Runs the clean-node container sharing the fresh skmem-pg container's
#      network namespace (so `localhost:5432` in the node-under-test reaches
#      the fresh Postgres) and executes, for REAL (no mocked Effects):
#        skos install skbrain --force
#        skbrain doctor
#        skbrain operator explain --json
#        skbrain operator observe --json
#   5. Writes the combined evidence (every command's stdout/stderr, exit
#      code, the parsed operator conditions, and a computed gate_pass
#      boolean) to a JSON artifact under the evidence dir and prints a
#      human summary.
#
# Exit codes:
#   0 - harness ran to completion AND the gate passed (all 4 operator
#       conditions report status "True").
#   1 - harness ran to completion but the gate FAILED (this is the expected
#       result today: two of skbrain's four operator conditions,
#       CmdbDriftBounded and KedbCanonCovered, are hardcoded "Unknown"
#       literals in src/skos/brain/ops/cli.py regardless of what actually
#       happened -- separate cards own fixing that; see the runbook).
#   2 - harness setup failure (docker missing, skmem-pg never became ready,
#       staging/build failure). Not a gate result either way.
#
# See docs/runbooks/skbrain-fresh-node-gate.md for what this isolation does
# and does NOT prove versus a truly bare machine.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

SKMEMORY_REPO="${SKMEMORY_REPO:-$HOME/clawd/skcapstone-repos/skmemory}"
SKMEM_PG_IMAGE="${SKMEM_PG_IMAGE:-smilintux/skmem-pg:pg17-bm25-age}"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)-$$"
EVIDENCE_DIR="${EVIDENCE_DIR:-$REPO_ROOT/artifacts/fresh-node-gate/$RUN_ID}"
STAGE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/skbrain-fresh-node-stage.XXXXXX")"
PG_PASSWORD="$(python3 -c 'import secrets; print(secrets.token_urlsafe(24))')"
NODE_IMAGE_TAG="skbrain-fresh-node:$RUN_ID"
PG_CONTAINER="skbrain-fresh-node-pg-$RUN_ID"

log() { printf '\n=== %s ===\n' "$*" >&2; }
cleanup() {
    docker rm -f "$PG_CONTAINER" >/dev/null 2>&1 || true
    docker image rm "$NODE_IMAGE_TAG" >/dev/null 2>&1 || true
    rm -rf "$STAGE_DIR"
}
trap cleanup EXIT

command -v docker >/dev/null 2>&1 || { echo "FATAL: docker not found on PATH" >&2; exit 2; }
if [ ! -d "$SKMEMORY_REPO/deploy/skmem-pg" ]; then
    echo "FATAL: SKMEMORY_REPO ($SKMEMORY_REPO) has no deploy/skmem-pg; set SKMEMORY_REPO to a skmemory checkout" >&2
    exit 2
fi
if ! docker image inspect "$SKMEM_PG_IMAGE" >/dev/null 2>&1; then
    echo "FATAL: docker image $SKMEM_PG_IMAGE not found locally." >&2
    echo "       Build it once from the skmemory checkout:" >&2
    echo "         docker build -t $SKMEM_PG_IMAGE $SKMEMORY_REPO/deploy/skmem-pg" >&2
    echo "       (compiles Apache AGE from source; can take several minutes)" >&2
    exit 2
fi

mkdir -p "$EVIDENCE_DIR"

log "staging skos worktree + skmemory checkout (read-only source, nothing mutated)"
mkdir -p "$STAGE_DIR/skos" "$STAGE_DIR/skmemory"
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    --exclude='.ruff_cache' --exclude='artifacts' \
    "$REPO_ROOT/" "$STAGE_DIR/skos/" \
    || { echo "FATAL: staging skos failed" >&2; exit 2; }
rsync -a --exclude='.git' --exclude='.venv' --exclude='__pycache__' --exclude='.pytest_cache' \
    "$SKMEMORY_REPO/" "$STAGE_DIR/skmemory/" \
    || { echo "FATAL: staging skmemory failed" >&2; exit 2; }
cp "$REPO_ROOT/docker/fresh-node/entrypoint.sh" "$STAGE_DIR/entrypoint.sh"

log "building the fresh-node image ($NODE_IMAGE_TAG)"
docker build -q -t "$NODE_IMAGE_TAG" -f "$REPO_ROOT/docker/fresh-node/Dockerfile" "$STAGE_DIR" >&2 \
    || { echo "FATAL: docker build failed" >&2; exit 2; }

log "booting an ephemeral, fresh skmem-pg ($PG_CONTAINER, fresh volume, not the host's real skmem-pg)"
# NOTE (real finding, see docs/runbooks/skbrain-fresh-node-gate.md
# "Follow-ups"): on a genuinely empty data volume, schema.sql's
# `CREATE EXTENSION ... WITH SCHEMA paradedb/ag_catalog` fails because
# Postgres 17 does not auto-create those target schemas even though the
# extensions declare them as fixed. docker/fresh-node/00-pre-schemas.sql is
# OUR harness's own bootstrap glue (not a change to the skmemory repo) that
# pre-creates them so the REAL skmemory init script can run to completion.
docker run -d --rm --name "$PG_CONTAINER" \
    -e POSTGRES_DB=skmemory \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" \
    -v "$REPO_ROOT/docker/fresh-node/00-pre-schemas.sql:/docker-entrypoint-initdb.d/00-pre-schemas.sql:ro" \
    -v "$SKMEMORY_REPO/deploy/skmem-pg/initdb/00-run-init.sh:/docker-entrypoint-initdb.d/00-run-init.sh:ro" \
    -v "$SKMEMORY_REPO/deploy/skmem-pg:/skmem-initdb-src:ro" \
    "$SKMEM_PG_IMAGE" postgres -c shared_preload_libraries=pg_search,age >/dev/null \
    || { echo "FATAL: could not start ephemeral skmem-pg" >&2; exit 2; }

log "running the clean-node install + doctor + observe (sharing $PG_CONTAINER's network)"
docker run --rm \
    --network "container:$PG_CONTAINER" \
    -e POSTGRES_PASSWORD="$PG_PASSWORD" \
    -v "$EVIDENCE_DIR:/evidence" \
    "$NODE_IMAGE_TAG"
NODE_RC=$?

if [ ! -f "$EVIDENCE_DIR/report.json" ]; then
    echo "FATAL: no evidence report was written (harness setup failure, node exit=$NODE_RC)" >&2
    exit 2
fi

GATE_PASS="$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('gate_pass'))" "$EVIDENCE_DIR/report.json")"

log "evidence: $EVIDENCE_DIR/report.json"
python3 -c "
import json
r = json.load(open('$EVIDENCE_DIR/report.json'))
print('harness_ok      :', r.get('harness_ok'))
print('install exit    :', r.get('steps', {}).get('install', {}).get('exit_code'))
print('doctor exit     :', r.get('steps', {}).get('doctor', {}).get('exit_code'))
print('observe exit    :', r.get('steps', {}).get('operator_observe', {}).get('exit_code'))
print('gate_conditions :')
for c in (r.get('gate_conditions') or []):
    print(f\"  - {c.get('type'):<20} {c.get('status'):<8} {c.get('reason')}\")
print('gate_pass       :', r.get('gate_pass'))
"

if [ "$GATE_PASS" = "True" ]; then
    log "GATE PASS"
    exit 0
else
    log "GATE FAIL (harness itself ran correctly -- see evidence for why the gate did not pass)"
    exit 1
fi
