# Fresh-node E2E gate image (card d17892ae, epic fb3cc09d).
#
# This is the "clean node under test" for the skbrain fresh-node E2E gate. It
# is built each run from a STAGED COPY of the skos worktree under test plus a
# copy of the skmemory repo checkout (see scripts/fresh-node-gate.sh, which
# assembles the build context at ../.stage/{skos,skmemory} before invoking
# `docker build`). Nothing here is baked into a long-lived image tag on
# purpose: every run installs skos + skmemory from source into a brand-new
# container filesystem, so $HOME (/root) starts genuinely empty -- no
# ~/.skcapstone, no ~/.skenv, no pre-seeded skvault, no prior pack state.
#
# What this proves: the REAL `skos install skbrain` code path (DefaultEffects,
# not the mocked Effects protocol used by tests/test_pack_provisioner.py) runs
# against a real, freshly-migrated Postgres (see scripts/fresh-node-gate.sh
# for the sibling skmem-pg container) and real subprocess calls to the
# `skmemory` CLI, real skvault file-backend crypto, real doctor/operator
# queries.
#
# What this does NOT prove: bare-metal-from-ISO provenance (still Debian
# userland inside a container, sharing the host kernel), network egress to
# the private skbrain-ops content repo (no credentials are available in this
# sandbox, so the content_repo install step is EXPECTED to fail here -- see
# the runbook), or systemd/cron wiring (fleet objects are written as files,
# never activated). See docs/runbooks/skbrain-fresh-node-gate.md.
FROM python:3.12-slim-bookworm

# postgresql-client-17, NOT Debian bookworm's default postgresql-client
# (v15): found by running this gate for real -- `pg_dump` refuses to talk to
# a newer major-version server ("aborting because of server version
# mismatch"), and skmem-pg runs Postgres 17. A node whose toolchain doesn't
# track skmem-pg's server version cannot even pre-dump before a migration.
# Pull it from the PGDG apt repo (bookworm's own repos only carry v15).
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates git curl gnupg \
    && install -d /usr/share/postgresql-common/pgdg \
    && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc \
       https://www.postgresql.org/media/keys/ACCC4CF8.asc \
    && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
       > /etc/apt/sources.list.d/pgdg.list \
    && apt-get update \
    && apt-get install -y --no-install-recommends postgresql-client-17 \
    && apt-get purge -y curl gnupg && apt-get autoremove -y \
    && rm -rf /var/lib/apt/lists/*

ENV HOME=/root \
    PYTHONUNBUFFERED=1 \
    PIP_ROOT_USER_ACTION=ignore

WORKDIR /root/clawd/skcapstone-repos

# Order matters for layer caching: skmemory changes less often than skos
# during this harness's own development.
COPY skmemory ./skmemory
COPY skos ./skos

# Real installs, in the same order + editable style the fleet convention
# uses (see ~/.claude/CLAUDE.md "SK* packages install into ~/.skenv/"): a
# sibling-repo checkout, `pip install -e .`. This is what makes
# `Path(__file__).resolve().parent.parent / "deploy" / "skmem-pg"` (skmemory's
# DEPLOY_DIR) and skos's own `resolve_package_path()` convention line up: both
# expect a real checkout at ~/clawd/skcapstone-repos/<package>, not a bare
# PyPI wheel (the PyPI skmemory wheel does NOT ship deploy/, confirmed during
# harness development -- see the runbook's follow-ups).
RUN pip install --no-cache-dir -e ./skmemory \
    && pip install --no-cache-dir -e ./skos

COPY entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

WORKDIR /root
ENTRYPOINT ["/entrypoint.sh"]
