#!/usr/bin/env bash
# Fresh-node gate entrypoint (card d17892ae). Runs INSIDE the "clean node"
# container, sharing the network namespace of a sibling, also-fresh skmem-pg
# container (see scripts/fresh-node-gate.sh) so `localhost:5432` reaches a
# real, freshly-initialized Postgres. Never touches anything outside this
# container's own filesystem.
#
# Deliberately does NOT `set -e`: a failing install step is an expected,
# useful observation, not a harness crash. Every step's exit code is
# captured and folded into the evidence JSON; the harness itself always
# exits 0 (it ran to completion) unless something earlier than "gate
# evaluation" breaks (e.g. Postgres never comes up).
set -uo pipefail

EVIDENCE_DIR="${EVIDENCE_DIR:-/evidence}"
mkdir -p "$EVIDENCE_DIR"
REPORT="$EVIDENCE_DIR/report.json"

log() { printf '[fresh-node] %s\n' "$*" >&2; }

log "waiting for skmem-pg (localhost:5432, shared netns) ..."
PG_READY=0
for i in $(seq 1 60); do
    if PGPASSWORD="${POSTGRES_PASSWORD:-}" psql -h localhost -U postgres -d skmemory -c 'select 1' >/dev/null 2>&1; then
        log "postgres ready after ${i}s"
        PG_READY=1
        break
    fi
    sleep 1
done

if [ "$PG_READY" -ne 1 ]; then
    log "FATAL: postgres never became ready; this is a harness setup failure, not a gate result"
    python3 - "$REPORT" <<'PYEOF'
import json, sys
json.dump({
    "harness": "skbrain-fresh-node-e2e-gate",
    "harness_ok": False,
    "harness_error": "skmem-pg did not become ready within 60s",
    "gate_pass": False,
}, open(sys.argv[1], "w"), indent=2, sort_keys=True)
PYEOF
    exit 2
fi

export SKMEMORY_PG_DSN="postgresql://postgres:${POSTGRES_PASSWORD:-}@localhost:5432/skmemory"

log "== skos install skbrain (real install path, real DefaultEffects) =="
INSTALL_OUT="$(mktemp)"
# --force: assert the requires gate (skmem-pg capability + sibling package
# versions) is satisfied. This node genuinely HAS a fresh skmem-pg reachable
# on localhost:5432 -- the gate's NodeFacts synthesis just has no way to
# discover that without the full skcapstone topology registry, which is out
# of scope for this harness. --force does NOT touch step execution: every
# step below still runs through real DefaultEffects (subprocess, psycopg,
# skvault file-backend crypto), never the mocked Effects protocol.
skos install skbrain --force >"$INSTALL_OUT" 2>&1
INSTALL_RC=$?
cat "$INSTALL_OUT" >&2

DROP_IN="$HOME/.config/environment.d/skbrain.conf"
if [ -f "$DROP_IN" ]; then
    log "sourcing credential drop-in written by the db_roles install step: $DROP_IN"
    set -a
    # shellcheck disable=SC1090
    source "$DROP_IN"
    set +a
else
    log "no credential drop-in at $DROP_IN (db_roles step did not complete); doctor/observe will read as ungranted"
fi

log "== skbrain doctor =="
DOCTOR_OUT="$(mktemp)"
skbrain doctor >"$DOCTOR_OUT" 2>&1
DOCTOR_RC=$?
cat "$DOCTOR_OUT" >&2

log "== skbrain operator explain =="
EXPLAIN_OUT="$(skbrain operator explain --json 2>&1)"
EXPLAIN_RC=$?

log "== skbrain operator observe (the fresh-node gate's own evidence) =="
OBSERVE_OUT="$(mktemp)"
skbrain operator observe --json >"$OBSERVE_OUT" 2>&1
OBSERVE_RC=$?
cat "$OBSERVE_OUT" >&2

python3 - "$REPORT" "$INSTALL_OUT" "$INSTALL_RC" "$DOCTOR_OUT" "$DOCTOR_RC" "$OBSERVE_OUT" "$OBSERVE_RC" "$EXPLAIN_RC" <<'PYEOF' "$EXPLAIN_OUT"
import json
import sys

report_path, install_out, install_rc, doctor_out, doctor_rc, observe_out, observe_rc, explain_rc = sys.argv[1:9]
explain_out = sys.argv[9] if len(sys.argv) > 9 else ""


def read(path: str) -> str:
    with open(path, encoding="utf-8", errors="replace") as fh:
        return fh.read()


observe_text = read(observe_out)
conditions = None
gate_pass = False
try:
    lines = [ln for ln in observe_text.strip().splitlines() if ln.strip()]
    payload = json.loads(lines[-1]) if lines else {}
    conditions = payload.get("conditions")
    if conditions:
        gate_pass = all(c.get("status") == "True" for c in conditions)
except Exception as exc:  # noqa: BLE001 -- best-effort parse, never crash evidence capture
    conditions = None
    gate_pass = False

report = {
    "harness": "skbrain-fresh-node-e2e-gate",
    "harness_ok": True,
    "card": "d17892ae",
    "epic": "fb3cc09d",
    "steps": {
        "install": {
            "command": "skos install skbrain --force",
            "exit_code": int(install_rc),
            "output": read(install_out),
        },
        "doctor": {
            "command": "skbrain doctor",
            "exit_code": int(doctor_rc),
            "output": read(doctor_out),
        },
        "operator_explain": {
            "command": "skbrain operator explain --json",
            "exit_code": int(explain_rc),
            "output": explain_out,
        },
        "operator_observe": {
            "command": "skbrain operator observe --json",
            "exit_code": int(observe_rc),
            "output": observe_text,
        },
    },
    "gate_conditions": conditions,
    "gate_pass": gate_pass,
}

with open(report_path, "w", encoding="utf-8") as fh:
    json.dump(report, fh, indent=2, sort_keys=True)

print(json.dumps({"gate_pass": gate_pass, "conditions": conditions}, indent=2))
PYEOF

log "evidence written to $REPORT"
exit 0
